"""
Unit tests for OSI to SQL Converter.

Tests the direct OSI → SQL conversion path including:
- Table DDL generation (Snowflake + ANSI dialects)
- Metric translation (override, dialect, simple agg, DAX)
- Semantic view generation with JOINs
- Error handling and edge cases
"""

import pytest
from unittest.mock import MagicMock

from semabridge.converter.osi_to_sql import convert_osi_to_sql, OSIToSQLResult
from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIRelationship,
    OSIDimension,
    OSIAttribute,
    OSIExpressionDialect,
    OSIDataType,
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def simple_model():
    """Minimal OSI model with one fact table and one metric."""
    return OSIModel(
        unique_name="test_model",
        label="Test Model",
        datasets=[
            OSIDataset(
                unique_name="Fact",
                label="Fact",
                source_table="FACT_TABLE",
                is_fact=True,
                columns=[
                    OSIColumn(unique_name="Revenue", data_type=OSIDataType.DECIMAL, is_key=True),
                    OSIColumn(unique_name="Quantity", data_type=OSIDataType.INTEGER),
                ],
            )
        ],
        metrics=[
            OSIMetric(
                unique_name="Total Revenue",
                dataset="Fact",
                source_column="Revenue",
                aggregation=OSIAggregationType.SUM,
            )
        ],
    )


@pytest.fixture
def star_schema_model():
    """OSI model with fact + dimension + relationship."""
    return OSIModel(
        unique_name="sales_model",
        label="Sales Model",
        datasets=[
            OSIDataset(
                unique_name="Fact_Sales",
                label="Sales Fact",
                source_table="FACT_SALES",
                is_fact=True,
                columns=[
                    OSIColumn(unique_name="Amount", data_type=OSIDataType.DECIMAL),
                    OSIColumn(unique_name="CustomerID", data_type=OSIDataType.INTEGER, is_key=True),
                ],
            ),
            OSIDataset(
                unique_name="Dim_Customer",
                label="Customer Dimension",
                source_table="DIM_CUSTOMER",
                is_fact=False,
                columns=[
                    OSIColumn(unique_name="ID", data_type=OSIDataType.INTEGER, is_key=True),
                    OSIColumn(unique_name="Name", data_type=OSIDataType.STRING),
                ],
            ),
        ],
        metrics=[
            OSIMetric(
                unique_name="Total Sales",
                dataset="Fact_Sales",
                source_column="Amount",
                aggregation=OSIAggregationType.SUM,
            )
        ],
        dimensions=[
            OSIDimension(
                unique_name="Customer",
                dataset="Dim_Customer",
                attributes=[
                    OSIAttribute(
                        unique_name="Customer Name",
                        dataset="Dim_Customer",
                        source_column="Name",
                    )
                ],
            )
        ],
        relationships=[
            OSIRelationship(
                unique_name="Sales_to_Customer",
                from_dataset="Fact_Sales",
                from_columns=["CustomerID"],
                to_dataset="Dim_Customer",
                to_columns=["ID"],
                cardinality=OSICardinality.MANY_TO_ONE,
            )
        ],
    )


# ============================================================================
# Table DDL Tests
# ============================================================================


class TestTableDDL:
    """Test CREATE TABLE generation."""

    def test_snowflake_table_ddl(self, simple_model):
        result = convert_osi_to_sql(simple_model, dialect="snowflake")
        assert len(result.source_table_ddls) == 1
        ddl = result.source_table_ddls[0]
        assert "CREATE OR REPLACE TABLE" in ddl
        assert "FACT_TABLE" in ddl
        assert "NUMBER(38,6)" in ddl  # DECIMAL maps to NUMBER(38,6)
        assert "NUMBER(38,0)" in ddl  # INTEGER maps to NUMBER(38,0)

    def test_ansi_table_ddl(self, simple_model):
        result = convert_osi_to_sql(simple_model, dialect="ansi")
        ddl = result.source_table_ddls[0]
        assert "DECIMAL(38,6)" in ddl
        assert "INTEGER" in ddl

    def test_no_source_tables_when_disabled(self, simple_model):
        result = convert_osi_to_sql(simple_model, include_source_tables=False)
        assert result.source_table_ddls == []

    def test_multiple_tables(self, star_schema_model):
        result = convert_osi_to_sql(star_schema_model)
        assert len(result.source_table_ddls) == 2

    def test_all_data_types_snowflake(self):
        """Verify all OSI data types map to Snowflake types."""
        columns = [
            OSIColumn(unique_name=f"col_{dt.name}", data_type=dt)
            for dt in OSIDataType
        ]
        model = OSIModel(
            unique_name="types_test",
            datasets=[
                OSIDataset(
                    unique_name="AllTypes",
                    source_table="ALL_TYPES",
                    is_fact=True,
                    columns=columns,
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="dummy",
                    dataset="AllTypes",
                    source_column="col_INTEGER",
                    aggregation=OSIAggregationType.SUM,
                )
            ],
        )
        result = convert_osi_to_sql(model, dialect="snowflake")
        ddl = result.source_table_ddls[0]
        assert "VARCHAR" in ddl
        assert "NUMBER(38,0)" in ddl
        assert "BOOLEAN" in ddl
        assert "DATE" in ddl
        assert "TIMESTAMP_NTZ" in ddl


# ============================================================================
# Metric Translation Tests
# ============================================================================


class TestMetricTranslation:
    """Test metric SQL expression generation."""

    def test_simple_sum_metric(self, simple_model):
        result = convert_osi_to_sql(simple_model)
        assert "Total Revenue" in result.metric_expressions
        expr = result.metric_expressions["Total Revenue"]
        assert "SUM" in expr
        assert "REVENUE" in expr

    def test_count_distinct_metric(self):
        model = OSIModel(
            unique_name="cd_test",
            datasets=[
                OSIDataset(
                    unique_name="Orders",
                    is_fact=True,
                    columns=[OSIColumn(unique_name="CustomerID", data_type=OSIDataType.INTEGER)],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Unique Customers",
                    dataset="Orders",
                    source_column="CustomerID",
                    aggregation=OSIAggregationType.COUNT_DISTINCT,
                )
            ],
        )
        result = convert_osi_to_sql(model)
        expr = result.metric_expressions["Unique Customers"]
        assert "COUNT(DISTINCT" in expr

    def test_override_metric_passthrough(self):
        """Metrics with sql_expression should be used directly."""
        model = OSIModel(
            unique_name="override_test",
            datasets=[
                OSIDataset(
                    unique_name="Fact",
                    is_fact=True,
                    columns=[OSIColumn(unique_name="Rev", data_type=OSIDataType.DECIMAL)],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Custom Metric",
                    dataset="Fact",
                    source_column="Rev",
                    sql_expression='SUM(CASE WHEN SCENARIO."SCENARIO" = \'Actual\' THEN FACT."REVENUE" ELSE 0 END)',
                    is_override=True,
                )
            ],
        )
        result = convert_osi_to_sql(model)
        expr = result.metric_expressions["Custom Metric"]
        assert "CASE WHEN" in expr
        assert "SCENARIO" in expr

    def test_dialect_metric_selection(self):
        """Should select the matching dialect expression."""
        model = OSIModel(
            unique_name="dialect_test",
            datasets=[
                OSIDataset(
                    unique_name="Fact",
                    is_fact=True,
                    columns=[OSIColumn(unique_name="Rev", data_type=OSIDataType.DECIMAL)],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Revenue",
                    dataset="Fact",
                    source_column="Rev",
                    dialects=[
                        OSIExpressionDialect(dialect="DAX", expression="SUM('Fact'[Rev])"),
                        OSIExpressionDialect(dialect="SNOWFLAKE_SQL", expression='SUM(FACT."REV")'),
                        OSIExpressionDialect(dialect="ANSI_SQL", expression='SUM(FACT."REV")'),
                    ],
                )
            ],
        )
        # Snowflake dialect should pick SNOWFLAKE_SQL
        result = convert_osi_to_sql(model, dialect="snowflake")
        assert result.metric_expressions["Revenue"] == 'SUM(FACT."REV")'

    def test_dax_metric_tier1_translation(self):
        """DAX expressions should be translated via DAXTranslator."""
        model = OSIModel(
            unique_name="dax_test",
            datasets=[
                OSIDataset(
                    unique_name="Fact",
                    is_fact=True,
                    columns=[OSIColumn(unique_name="Revenue", data_type=OSIDataType.DECIMAL)],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Total",
                    dataset="Fact",
                    expression="SUM('Fact'[Revenue])",
                )
            ],
        )
        result = convert_osi_to_sql(model)
        assert "Total" in result.metric_expressions
        assert "SUM" in result.metric_expressions["Total"]

    def test_untranslatable_metric_warning(self):
        """Metrics that can't be translated should produce warnings."""
        model = OSIModel(
            unique_name="warn_test",
            datasets=[
                OSIDataset(
                    unique_name="Fact",
                    is_fact=True,
                    columns=[OSIColumn(unique_name="Rev", data_type=OSIDataType.DECIMAL)],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Complex Metric",
                    dataset="Fact",
                    expression="CALCULATE(SUMX(FILTER(ALL(Scenario), Scenario[Name]=\"Budget\"), EARLIER([Amount])))",
                )
            ],
        )
        result = convert_osi_to_sql(model)
        assert "Complex Metric" not in result.metric_expressions
        assert any("Complex Metric" in w for w in result.warnings)


# ============================================================================
# Semantic View Tests
# ============================================================================


class TestSemanticView:
    """Test CREATE VIEW generation."""

    def test_view_name(self, simple_model):
        result = convert_osi_to_sql(simple_model)
        assert "CREATE OR REPLACE VIEW" in result.semantic_view_ddl
        assert "V_TEST_MODEL" in result.semantic_view_ddl

    def test_view_contains_metric(self, simple_model):
        result = convert_osi_to_sql(simple_model)
        assert "TOTAL_REVENUE" in result.semantic_view_ddl

    def test_view_with_join(self, star_schema_model):
        result = convert_osi_to_sql(star_schema_model)
        ddl = result.semantic_view_ddl
        assert "LEFT JOIN" in ddl
        assert "DIM_CUSTOMER" in ddl

    def test_view_with_dimension_columns(self, star_schema_model):
        result = convert_osi_to_sql(star_schema_model)
        ddl = result.semantic_view_ddl
        # Should include dimension attribute in SELECT
        assert "NAME" in ddl
        # Should have GROUP BY
        assert "GROUP BY" in ddl

    def test_empty_model_graceful(self):
        model = OSIModel(unique_name="empty")
        result = convert_osi_to_sql(model)
        # Should not crash, may produce a comment
        assert result.semantic_view_ddl is not None


# ============================================================================
# Error Handling Tests
# ============================================================================


class TestErrorHandling:
    """Test error conditions."""

    def test_unsupported_dialect(self, simple_model):
        with pytest.raises(ConversionError, match="Unsupported SQL dialect"):
            convert_osi_to_sql(simple_model, dialect="oracle")

    def test_result_repr(self, simple_model):
        result = convert_osi_to_sql(simple_model)
        r = repr(result)
        assert "snowflake" in r
        assert "tables=" in r

    def test_all_ddl_concatenation(self, simple_model):
        result = convert_osi_to_sql(simple_model)
        all_sql = result.all_ddl
        assert "CREATE OR REPLACE TABLE" in all_sql
        assert "CREATE OR REPLACE VIEW" in all_sql


# ============================================================================
# Standalone convert_osi_to_sml function Tests
# ============================================================================


class TestConvertOSIToSMLFunction:
    """Test the standalone convert_osi_to_sml wrapper function."""

    def test_returns_sml_model(self):
        from semabridge.converter.osi_to_sml import convert_osi_to_sml
        from semabridge.formats.sml.models import SMLModel

        osi = OSIModel(
            unique_name="func_test",
            label="Function Test",
            source_platform="fabric",
            datasets=[
                OSIDataset(
                    unique_name="Sales",
                    is_fact=True,
                    columns=[
                        OSIColumn(unique_name="Amount", data_type=OSIDataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Total Amount",
                    dataset="Sales",
                    expression="SUM('Sales'[Amount])",
                )
            ],
        )
        sml = convert_osi_to_sml(osi)
        assert isinstance(sml, SMLModel)
        assert sml.unique_name == "func_test"
        assert len(sml.datasets) >= 1  # May include injected calendar
        assert len(sml.metrics) == 1
        assert sml.metrics[0].unique_name == "Total Amount"

    def test_matches_class_output(self):
        """Standalone function should produce same result as class."""
        from semabridge.converter.osi_to_sml import convert_osi_to_sml, OSIToSMLConverter

        osi = OSIModel(
            unique_name="compare_test",
            label="Compare Test",
            source_platform="fabric",
            datasets=[
                OSIDataset(
                    unique_name="Orders",
                    is_fact=True,
                    columns=[
                        OSIColumn(unique_name="Total", data_type=OSIDataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Sum Total",
                    dataset="Orders",
                    expression="SUM('Orders'[Total])",
                )
            ],
        )

        sml_func = convert_osi_to_sml(osi)
        sml_class = OSIToSMLConverter(llm_config=False).from_osi(osi)

        assert sml_func.unique_name == sml_class.unique_name
        assert len(sml_func.metrics) == len(sml_class.metrics)
        assert sml_func.metrics[0].sql_expression == sml_class.metrics[0].sql_expression
