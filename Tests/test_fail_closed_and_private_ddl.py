"""Tests for fail-closed partial translations and PRIVATE DDL declarations.

1. Placeholder inside IF / COALESCE / arithmetic -> parent is a placeholder.
2. CALCULATE(<untranslatable measure>, ALL(col)) -> parent is a placeholder.
3. COUNTROWS(FILTER(T, [col] = ...)) with hidden columns keeps the predicate -> COUNT(CASE WHEN ... THEN 1 END).
4. Hidden columns and tables -> PRIVATE in the DDL.
5. Helper columns from enriched views and helper metrics -> PRIVATE in the DDL.
"""

from __future__ import annotations
import pytest
from unittest.mock import MagicMock

from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    AggregationType,
)
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig
from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
from semabridge.converter.dax_translator import DAXTranslator


def test_placeholder_inside_if_coalesce_arithmetic_makes_parent_placeholder():
    """Item 1: A placeholder inside IF / COALESCE / arithmetic makes the parent a placeholder."""
    renderer = DaxSqlRenderer(
        table_alias="SALES",
        primary_table_name="Sales",
        measure_sql_map={"untranslatable_m": "CAST(NULL AS DOUBLE)"},
        known_measure_names={"untranslatable_m"},
    )
    parser = DaxAstParser()

    # 1. Inside IF condition / branch
    ast_if = parser.parse("IF([untranslatable_m] > 0, 1, 0)")
    assert renderer.render(ast_if) is None
    assert "depends on untranslatable part" in (renderer.last_render_error or "")
    with pytest.raises(Exception) as exc_if:
        renderer._render_node(ast_if)
    assert "depends on untranslatable part" in str(exc_if.value)

    # 2. Inside COALESCE
    ast_coalesce = parser.parse("COALESCE([untranslatable_m], 0)")
    assert renderer.render(ast_coalesce) is None
    assert "depends on untranslatable part" in (renderer.last_render_error or "")
    with pytest.raises(Exception) as exc_coal:
        renderer._render_node(ast_coalesce)
    assert "depends on untranslatable part" in str(exc_coal.value)

    # 3. Inside arithmetic
    ast_arith = parser.parse("[untranslatable_m] + 10")
    assert renderer.render(ast_arith) is None
    assert "depends on untranslatable part" in (renderer.last_render_error or "")
    with pytest.raises(Exception) as exc_arith:
        renderer._render_node(ast_arith)
    assert "depends on untranslatable part" in str(exc_arith.value)


def test_calculate_untranslatable_measure_with_all_makes_parent_placeholder():
    """Item 1: CALCULATE(<untranslatable measure>, ALL(col)) -> parent is a placeholder."""
    renderer = DaxSqlRenderer(
        table_alias="SALES",
        primary_table_name="Sales",
        measure_sql_map={"untranslatable_m": "CAST(NULL AS DOUBLE)"},
        known_measure_names={"untranslatable_m"},
    )
    parser = DaxAstParser()

    ast_calc = parser.parse("CALCULATE([untranslatable_m], ALL('Sales'[Region]))")
    assert renderer.render(ast_calc) is None
    assert "depends on untranslatable part" in (renderer.last_render_error or "")
    with pytest.raises(Exception) as exc:
        renderer._render_node(ast_calc)
    assert "depends on untranslatable part" in str(exc.value)


def test_countrows_filter_with_hidden_column_keeps_predicate():
    """Item 2: COUNTROWS(FILTER(T, [col] = ...)) with unqualified or hidden columns keeps predicate."""
    renderer = DaxSqlRenderer(
        table_alias="ORDERS",
        primary_table_name="Orders",
        table_aliases={"Orders": "ORDERS"},
        dataset_col_lookup={"Orders": {"status", "hidden_flag"}},
    )
    parser = DaxAstParser()

    ast = parser.parse("COUNTROWS(FILTER('Orders', [hidden_flag] = 1))")
    sql = renderer.render(ast)
    # Must keep the predicate in a CASE statement, never drop to COUNT(*)
    assert sql == 'COUNT(CASE WHEN ORDERS."HIDDEN_FLAG" = 1 THEN 1 END)'


def test_hidden_columns_and_tables_declared_private_in_ddl():
    """Item 3: Hidden PBIX columns and tables are declared PRIVATE in the semantic view."""
    cfg = SnowflakeConfig(
        account="test", user="test", database="DB", schema_name="SCHEMA"
    )
    emitter = SnowflakeEmitter(cfg)

    # Table 1: visible table with 1 visible column and 1 hidden column
    # Table 2: hidden table with 1 column
    model = SMLModel(
        unique_name="TestModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                source_table="SALES_TABLE",
                is_hidden=False,
                columns=[
                    SMLColumn(unique_name="Id", data_type="integer", is_hidden=False),
                    SMLColumn(unique_name="SecretKey", data_type="string", is_hidden=True),
                ],
            ),
            SMLDataset(
                unique_name="InternalLog",
                source_table="INTERNAL_LOG_TABLE",
                is_hidden=True,
                columns=[
                    SMLColumn(unique_name="LogId", data_type="integer", is_hidden=False),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="TotalSales",
                dataset="Sales",
                expression="SUM('Sales'[Id])",
                sql_expression='SUM(SALES."ID")',
                aggregation=AggregationType.SUM,
                sync_enabled=True,
            )
        ],
    )

    ddls = emitter.generate_ddls(model)
    assert len(ddls) >= 1
    ddl = ddls[0]

    # SecretKey (hidden column on Sales) must be declared PRIVATE
    assert 'PRIVATE SALES."SECRETKEY" AS' in ddl or 'PRIVATE SALES."SecretKey" AS' in ddl or 'PRIVATE SALES."SECRET_KEY" AS' in ddl

    # InternalLog (hidden table) column must be declared PRIVATE
    assert 'PRIVATE INTERNALLOG."LOGID" AS' in ddl or 'PRIVATE INTERNALLOG."LogId" AS' in ddl or 'PRIVATE INTERNALLOG."LOG_ID" AS' in ddl

    # Id (visible column on Sales) must NOT be declared PRIVATE
    assert '  SALES."ID" AS' in ddl or '  SALES."Id" AS' in ddl


def test_helper_columns_and_metrics_declared_private_in_ddl():
    """Item 4: Helper columns from enriched views and helper metrics are always PRIVATE."""
    cfg = SnowflakeConfig(
        account="test", user="test", database="DB", schema_name="SCHEMA"
    )
    emitter = SnowflakeEmitter(cfg)

    model = SMLModel(
        unique_name="TestHelpersModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                source_table="SALES_TABLE_ENRICHED",
                is_hidden=False,
                columns=[
                    SMLColumn(unique_name="Amount", data_type="float", is_hidden=False),
                    # Enriched view helper column
                    SMLColumn(unique_name="IS_YTD", data_type="boolean", is_hidden=False),
                    SMLColumn(unique_name="MAX_DATE", data_type="date", is_hidden=False),
                ],
            ),
        ],
        metrics=[
            # Helper metric (_NUM / _DEN or _SUM / _CNT)
            SMLMetric(
                unique_name="BaseAmount_SUM",
                dataset="Sales",
                expression="SUM('Sales'[Amount])",
                sql_expression='SUM(SALES."AMOUNT")',
                aggregation=AggregationType.SUM,
                sync_enabled=True,
            ),
            SMLMetric(
                unique_name="BaseAmount_CNT",
                dataset="Sales",
                expression="COUNT('Sales'[Amount])",
                sql_expression='COUNT(SALES."AMOUNT")',
                aggregation=AggregationType.COUNT,
                sync_enabled=True,
            ),
            # Normal metric
            SMLMetric(
                unique_name="TotalAmount",
                dataset="Sales",
                expression="SUM('Sales'[Amount])",
                sql_expression='SUM(SALES."AMOUNT")',
                aggregation=AggregationType.SUM,
                sync_enabled=True,
            ),
        ],
    )

    ddls = emitter.generate_ddls(model)
    assert len(ddls) >= 1
    ddl = ddls[0]

    # Helper columns must be declared PRIVATE
    assert 'PRIVATE SALES."IS_YTD" AS' in ddl
    assert 'PRIVATE SALES."MAX_DATE" AS' in ddl

    # Helper metrics must be declared PRIVATE
    assert 'PRIVATE SALES."BASEAMOUNT_SUM" AS' in ddl or 'PRIVATE SALES."BaseAmount_SUM" AS' in ddl
    assert 'PRIVATE SALES."BASEAMOUNT_CNT" AS' in ddl or 'PRIVATE SALES."BaseAmount_CNT" AS' in ddl

    # Normal metric must NOT be declared PRIVATE
    assert '  SALES."TOTALAMOUNT" AS' in ddl or '  SALES."TotalAmount" AS' in ddl
