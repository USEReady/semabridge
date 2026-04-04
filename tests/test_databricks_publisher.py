"""Tests for DatabricksPublisher — metadata table + measure views."""

import re

import pytest

from semabridge.connectors.databricks_publisher import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    DEPLOY_REASON_CROSS_TABLE,
    DEPLOY_REASON_DAX_NOT_SUPPORTED,
    DEPLOY_REASON_VALIDATION_FAILED,
    DEPLOY_STATUS_DEPLOYED,
    DEPLOY_STATUS_NOT_DEPLOYED,
    TRANSLATION_TYPE_AGGREGATION_BUILT,
    TRANSLATION_TYPE_DAX_SKIPPED,
    TRANSLATION_TYPE_DAX_TRANSLATED,
    TRANSLATION_TYPE_SQL_NATIVE,
    VIEW_TYPE_METRIC,
    VIEW_TYPE_SQL,
    DatabricksPublisher,
    ResolvedMeasure,
)
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import (
    AggregationType,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _cfg() -> DatabricksConfig:
    return DatabricksConfig(
        host="dbc-b3ffac48-2f5a.cloud.databricks.com",
        token="dummy",
        warehouse_id="wh-1",
        catalog="main",
        schema_name="public",
    )


def _sales_model(
    *,
    with_sql_expression: bool = False,
    with_source_column: bool = False,
    with_group_by: bool = False,
    dax_only: bool = False,
) -> SMLModel:
    """Build a small SML model for testing various measure configurations."""
    metrics: list[SMLMetric] = []

    if with_sql_expression:
        metrics.append(
            SMLMetric(
                unique_name="Total_Revenue",
                dataset="Sales",
                sql_expression="SUM(`REVENUE`)",
                aggregation=AggregationType.SUM,
                group_by_dimensions=["REGION"] if with_group_by else [],
            )
        )
    elif with_source_column:
        metrics.append(
            SMLMetric(
                unique_name="Total_Revenue",
                dataset="Sales",
                source_column="REVENUE",
                aggregation=AggregationType.SUM,
                group_by_dimensions=["REGION"] if with_group_by else [],
            )
        )
    elif dax_only:
        metrics.append(
            SMLMetric(
                unique_name="Total_Revenue",
                dataset="Sales",
                expression="CALCULATE(SUM('Sales'[Revenue]), ALL('Date'))",
                aggregation=AggregationType.SUM,
            )
        )

    return SMLModel(
        unique_name="SalesModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="REGION", data_type=DataType.STRING),
                    SMLColumn(unique_name="DATE", data_type=DataType.DATE),
                ],
            )
        ],
        metrics=metrics,
    )


# ── Original Tests (Preserved) ──────────────────────────────────────────────

def test_generate_sql_statements_creates_single_model_table_with_rows():
    cfg = _cfg()
    model = SMLModel(
        unique_name="annual",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="region", data_type=DataType.STRING),
                    SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="revenue", dataset="Sales"),
        ],
    )

    sql = "\n".join(DatabricksPublisher(cfg).generate_sql_statements(model))

    assert "SEMABRIDGE_MODEL_SNAPSHOT" not in sql
    assert "DROP TABLE IF EXISTS `main`.`public`.`annual`" in sql
    assert "CREATE TABLE `main`.`public`.`annual`" in sql
    assert "'Sales', 'region', 'dimension'" in sql
    assert "'Sales', 'revenue', 'measure'" in sql


def test_generate_sql_statements_uses_int_for_calendar_dimensions():
    cfg = _cfg()
    model = SMLModel(
        unique_name="continent",
        datasets=[
            SMLDataset(
                unique_name="Date",
                columns=[
                    SMLColumn(unique_name="Year", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Quarter", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Month", data_type=DataType.INTEGER),
                ],
            )
        ],
    )

    sql = "\n".join(DatabricksPublisher(cfg).generate_sql_statements(model))

    assert "'Year', 'dimension', 'INT'" in sql
    assert "'Quarter', 'dimension', 'INT'" in sql
    assert "'Month', 'dimension', 'INT'" in sql


def test_generate_sql_statements_sanitizes_invalid_identifiers():
    cfg = _cfg()
    model = SMLModel(
        unique_name="continent",
        datasets=[
            SMLDataset(
                unique_name="continent 1",
                columns=[
                    SMLColumn(unique_name="country.name", data_type=DataType.STRING),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="total sales$", dataset="continent 1"),
        ],
    )

    sql = "\n".join(DatabricksPublisher(cfg).generate_sql_statements(model))

    assert "DROP TABLE IF EXISTS `main`.`public`.`continent`" in sql
    assert "CREATE TABLE `main`.`public`.`continent`" in sql
    assert "'continent_1', 'country_name', 'dimension'" in sql
    assert "'continent_1', 'total_sales', 'measure'" in sql


# ── Measure View Tests ───────────────────────────────────────────────────────

class TestMeasureViewGeneration:
    """Verify measure view SQL generation."""

    def test_sql_expression_priority_creates_view(self):
        """sql_expression takes highest priority and produces a CREATE VIEW."""
        model = _sales_model(with_sql_expression=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert skipped == 0
        assert len(stmts) == 1
        assert "CREATE OR REPLACE VIEW" in stmts[0]
        assert "SUM(`REVENUE`)" in stmts[0]
        assert "`mv_SalesModel_Total_Revenue`" in stmts[0]

    def test_aggregation_source_column_creates_view(self):
        """aggregation + source_column builds a view when no sql_expression."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert skipped == 0
        assert "SUM(`REVENUE`)" in stmts[0]

    def test_simple_dax_translated_creates_view(self):
        """Simple DAX like SUM('Table'[Col]) gets translated and produces a view."""
        model = SMLModel(
            unique_name="DaxModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total_Revenue",
                    dataset="Sales",
                    expression="SUM('Sales'[REVENUE])",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert skipped == 0
        assert "SUM(`REVENUE`)" in stmts[0]
        assert "CREATE OR REPLACE VIEW" in stmts[0]

    def test_complex_dax_skipped_no_view(self):
        """Complex DAX (CALCULATE, IF, etc.) is skipped — no view created."""
        model = _sales_model(dax_only=True)  # Uses CALCULATE — too complex
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 1
        assert len(stmts) == 0
        assert details[0]["reason"] == DEPLOY_REASON_DAX_NOT_SUPPORTED

    def test_no_group_by_pure_aggregate(self):
        """No group_by_dimensions → pure aggregate (no GROUP BY clause)."""
        model = _sales_model(with_sql_expression=True, with_group_by=False)
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(model)

        assert len(stmts) == 1
        assert "GROUP BY" not in stmts[0]

    def test_explicit_group_by_dimensions(self):
        """group_by_dimensions specified → GROUP BY those columns only."""
        model = _sales_model(with_sql_expression=True, with_group_by=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(model)

        assert len(stmts) == 1
        assert "GROUP BY `REGION`" in stmts[0]
        assert "`REGION`" in stmts[0].split("SELECT")[1].split("FROM")[0]  # In SELECT clause

    def test_view_naming_convention(self):
        """View names follow mv_<model>_<measure> convention."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(model)

        assert "`main`.`public`.`mv_SalesModel_Total_Revenue`" in stmts[0]

    def test_special_chars_in_names_sanitized(self):
        """Special characters in measure names are sanitized for SQL safety."""
        model = SMLModel(
            unique_name="My Model!",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total ($)",
                    dataset="Sales",
                    source_column="AMT",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(model)

        # No special characters in view name
        view_name_match = re.search(r'`mv_([^`]+)`', stmts[0])
        assert view_name_match
        assert "$" not in view_name_match.group(1)
        assert "!" not in view_name_match.group(1)


class TestDependencyValidation:
    """Verify dependency validation catches bad references."""

    def test_missing_dataset_skips_view(self):
        """Measure referencing non-existent dataset is skipped."""
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="bad_measure",
                    dataset="NonExistent",
                    source_column="AMT",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 1
        assert details[0]["reason"] == DEPLOY_REASON_VALIDATION_FAILED

    def test_missing_source_column_skips_view(self):
        """Measure referencing non-existent column is skipped."""
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="bad_col_measure",
                    dataset="Sales",
                    source_column="MISSING_COLUMN",
                    aggregation=AggregationType.SUM,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 1
        assert details[0]["reason"] == DEPLOY_REASON_VALIDATION_FAILED

    def test_cross_table_measure_skipped(self):
        """Measures with multi-table SQL references are skipped in v1."""
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                ),
                SMLDataset(
                    unique_name="Customers",
                    columns=[SMLColumn(unique_name="REGION", data_type=DataType.STRING)],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="cross_measure",
                    dataset="Sales",
                    sql_expression="SUM(sales.AMT) + COUNT(customers.REGION)",
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 1
        assert details[0]["reason"] == DEPLOY_REASON_CROSS_TABLE


class TestMetadataTableDeployStatus:
    """Verify deploy_status and deploy_reason in metadata table rows."""

    def test_deployed_measure_has_deployed_status(self):
        """Measures with valid SQL have deploy_status=DEPLOYED in metadata."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert f"'{DEPLOY_STATUS_DEPLOYED}'" in sql

    def test_dax_only_measure_has_not_deployed_status(self):
        """DAX-only measures have deploy_status=NOT_DEPLOYED in metadata."""
        model = _sales_model(dax_only=True)
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert f"'{DEPLOY_STATUS_NOT_DEPLOYED}'" in sql
        assert DEPLOY_REASON_DAX_NOT_SUPPORTED in sql

    def test_metadata_table_has_status_and_translation_columns(self):
        """Metadata table DDL includes deploy_status, deploy_reason, and translation_type."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts = publisher.generate_sql_statements(model)
        create_table = next(s for s in stmts if s.startswith("CREATE TABLE"))

        assert "deploy_status STRING" in create_table
        assert "deploy_reason STRING" in create_table
        assert "translation_type STRING" in create_table

    def test_deployed_measure_has_translation_type_in_metadata(self):
        """Deployed measures include translation_type in INSERT row."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert TRANSLATION_TYPE_AGGREGATION_BUILT in sql

    def test_sql_native_translation_type(self):
        """Measures with sql_expression get SQL_NATIVE translation type."""
        model = _sales_model(with_sql_expression=True)
        publisher = DatabricksPublisher(_cfg())

        sql = "\n".join(publisher.generate_sql_statements(model))

        assert TRANSLATION_TYPE_SQL_NATIVE in sql

    def test_schema_evolution_uses_drop_create(self):
        """Schema evolution uses DROP+CREATE instead of CREATE IF NOT EXISTS."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts = publisher.generate_sql_statements(model)

        assert any("DROP TABLE IF EXISTS" in s for s in stmts)
        assert not any("CREATE TABLE IF NOT EXISTS" in s for s in stmts)


class TestCombinedViewMode:
    """Verify combined view mode groups measures by dataset."""

    def test_combined_view_single_dataset(self):
        """Combined mode creates one view per dataset with all measures."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(measure_view_mode="combined")
        )
        model = SMLModel(
            unique_name="MyModel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="UNITS", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total_Revenue",
                    dataset="Sales",
                    source_column="REVENUE",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="Total_Units",
                    dataset="Sales",
                    source_column="UNITS",
                    aggregation=AggregationType.SUM,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 1  # One combined view (not two)
        assert skipped == 0
        assert "SUM(`REVENUE`) AS `Total_Revenue`" in stmts[0]
        assert "SUM(`UNITS`) AS `Total_Units`" in stmts[0]
        assert "measures" in stmts[0]  # View name contains 'measures' suffix

    def test_views_disabled_via_behavior(self):
        """Setting create_measure_views=false produces no views."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(create_measure_views=False)
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 0
        assert len(stmts) == 0

    def test_dax_translation_disabled_via_feature_flag(self):
        """When enable_simple_dax_translation=false, simple DAX is skipped."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_simple_dax_translation=False)
        )
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="total",
                    dataset="Sales",
                    expression="SUM('Sales'[AMT])",
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 1  # Skipped because translation is disabled


class TestAggregationTypes:
    """Verify all aggregation types generate correct SQL."""

    @pytest.mark.parametrize(
        "agg_type,expected_sql",
        [
            (AggregationType.SUM, "SUM(`AMT`)"),
            (AggregationType.COUNT, "COUNT(`AMT`)"),
            (AggregationType.COUNT_DISTINCT, "COUNT(DISTINCT `AMT`)"),
            (AggregationType.AVG, "AVG(`AMT`)"),
            (AggregationType.MIN, "MIN(`AMT`)"),
            (AggregationType.MAX, "MAX(`AMT`)"),
        ],
    )
    def test_aggregation_type_generates_correct_sql(self, agg_type, expected_sql):
        """Each aggregation type maps to the correct SQL function."""
        model = SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Facts",
                    columns=[SMLColumn(unique_name="AMT", data_type=DataType.DECIMAL)],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="metric_1",
                    dataset="Facts",
                    source_column="AMT",
                    aggregation=agg_type,
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(model)

        assert created == 1
        assert expected_sql in stmts[0]


class TestEmptyEdgeCases:
    """Verify edge cases with empty models."""

    def test_no_metrics_produces_no_views(self):
        """Model with no metrics produces no view statements."""
        model = SMLModel(
            unique_name="empty",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[SMLColumn(unique_name="X", data_type=DataType.STRING)],
                )
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0
        assert skipped == 0
        assert len(stmts) == 0

    def test_batch_insert_count_matches_objects(self):
        """All dimensions + measures appear in metadata INSERT rows."""
        model = SMLModel(
            unique_name="batch_model",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="region", data_type=DataType.STRING),
                        SMLColumn(unique_name="year", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(unique_name="revenue", dataset="Sales"),
                SMLMetric(unique_name="margin", dataset="Sales"),
            ],
        )

        statements = DatabricksPublisher(_cfg()).generate_sql_statements(model)
        insert_statements = [s for s in statements if s.startswith("INSERT INTO `main`.`public`.`batch_model`")]

        # One batched INSERT for all rows
        assert len(insert_statements) == 1
        # 2 dimensions + 2 measures = 4 rows → 4 current_timestamp() calls
        assert insert_statements[0].count("current_timestamp()") == 4


class TestDaxToSqlTranslation:
    """Verify simple DAX-to-SQL translation for Databricks views."""

    def _make_metric_model(self, dax_expression: str) -> SMLModel:
        return SMLModel(
            unique_name="test",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="AMOUNT", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="REGION", data_type=DataType.STRING),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="metric_1",
                    dataset="Sales",
                    expression=dax_expression,
                )
            ],
        )

    @pytest.mark.parametrize(
        "dax,expected_sql",
        [
            ("SUM('Sales'[AMOUNT])", "SUM(`AMOUNT`)"),
            ("COUNT('Sales'[AMOUNT])", "COUNT(`AMOUNT`)"),
            ("AVERAGE('Sales'[AMOUNT])", "AVG(`AMOUNT`)"),
            ("MIN('Sales'[AMOUNT])", "MIN(`AMOUNT`)"),
            ("MAX('Sales'[AMOUNT])", "MAX(`AMOUNT`)"),
            ("DISTINCTCOUNT('Sales'[AMOUNT])", "COUNT(DISTINCT `AMOUNT`)"),
            ("COUNTROWS('Sales')", "COUNT(*)"),
            ("COUNTBLANK('Sales'[AMOUNT])", "COUNT_IF(`AMOUNT` IS NULL)"),
            # Without table qualifier
            ("SUM([AMOUNT])", "SUM(`AMOUNT`)"),
            ("COUNT([AMOUNT])", "COUNT(`AMOUNT`)"),
        ],
    )
    def test_simple_dax_patterns_translate(self, dax, expected_sql):
        """Each simple DAX pattern maps to correct Databricks SQL."""
        model = self._make_metric_model(dax)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(model)

        assert created == 1, f"Expected view creation for DAX: {dax}"
        assert expected_sql in stmts[0], f"Expected '{expected_sql}' in SQL for DAX: {dax}"

    @pytest.mark.parametrize(
        "complex_dax",
        [
            "CALCULATE(SUM('Sales'[AMOUNT]), ALL('Date'))",
            "IF(ISBLANK([Revenue]), 0, [Revenue])",
            "SUMX('Sales', 'Sales'[Price] * 'Sales'[Qty])",
            "DIVIDE([Revenue], [Units], 0)",
        ],
    )
    def test_complex_dax_not_translated(self, complex_dax):
        """Complex DAX patterns are NOT translated — measure is skipped."""
        model = self._make_metric_model(complex_dax)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0, f"Should not create view for complex DAX: {complex_dax}"
        assert skipped == 1

    @pytest.mark.parametrize(
        "invalid_dax",
        [
            "SUM('Sales'[Revenue] + 10)",       # Arithmetic inside aggregation
            "SUM('Sales'[Revenue]) / 100",       # Post-aggregation arithmetic
            "SUM('Sales'[Revenue]) + SUM('Sales'[Cost])",  # Multi-aggregation
            "",                                  # Empty expression
            "   ",                               # Whitespace only
        ],
    )
    def test_invalid_dax_edge_cases_rejected(self, invalid_dax):
        """Edge case DAX patterns must NOT produce views (risk of broken SQL)."""
        model = self._make_metric_model(invalid_dax)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, details = publisher.generate_measure_view_statements(model)

        assert created == 0, f"Should reject invalid DAX: '{invalid_dax}'"


class TestMetricViewGeneration:
    """Tests for native Databricks Metric View (WITH METRICS LANGUAGE YAML)."""

    def test_metric_view_yaml_contains_version(self):
        """Generated YAML includes version 1.1 per Databricks spec."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert created >= 1
        assert "version: 1.1" in stmts[0]

    def test_metric_view_has_with_metrics_syntax(self):
        """Generated SQL uses WITH METRICS LANGUAGE YAML syntax."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert created >= 1
        assert "WITH METRICS LANGUAGE YAML" in stmts[0]
        assert "$$" in stmts[0]

    def test_metric_view_contains_dimensions(self):
        """Generated YAML includes dataset dimensions."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert created >= 1
        assert "dimensions:" in stmts[0]
        assert "REVENUE" in stmts[0]

    def test_metric_view_contains_measures(self):
        """Generated YAML includes resolved measures."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert created >= 1
        assert "measures:" in stmts[0]
        assert "SUM" in stmts[0]

    def test_metric_view_source_inline_sql_when_no_mapping(self):
        """Without source_table_mapping, source is inline SQL query."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, _, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        # Without mapping, source uses inline SQL with CAST(NULL AS type)
        assert "source: |" in stmts[0]
        assert "SELECT" in stmts[0]
        assert "CAST(NULL AS" in stmts[0]

    def test_metric_view_source_table_mapping(self):
        """Explicit source_table_mapping overrides default."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                source_table_mapping={"Sales": "analytics.raw.sales_data"},
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, _, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        assert "source: analytics.raw.sales_data" in stmts[0]

    def test_metric_view_source_catalog_schema_no_mapping(self):
        """Without explicit mapping, source_catalog/schema are NOT used for metric view source."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(
                source_catalog="my_catalog",
                source_schema="my_schema",
            )
        )
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg(), behavior=behavior)

        stmts, _, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        # Without explicit mapping, inline SQL is used (not catalog/schema)
        assert "source: |" in stmts[0]
        assert "SELECT" in stmts[0]

    def test_metric_view_view_type_none_returns_empty(self):
        """measure_view_type='none' skips all view generation."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, skipped, _ = publisher.generate_measure_view_statements(
            model, view_type_override="none"
        )

        assert created == 0
        assert len(stmts) == 0

    def test_sql_view_fallback_still_works(self):
        """view_type_override='sql_view' uses legacy SQL view generation."""
        model = _sales_model(with_source_column=True)
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="sql_view"
        )

        assert created >= 1
        # SQL views use plain SELECT, not YAML
        assert "WITH METRICS" not in stmts[0]
        assert "SELECT" in stmts[0]

    def test_confidence_high_for_native_sql(self):
        """SQL_NATIVE and AGGREGATION_BUILT get HIGH confidence."""
        publisher = DatabricksPublisher(_cfg())
        assert publisher._assess_confidence(TRANSLATION_TYPE_SQL_NATIVE) == CONFIDENCE_HIGH
        assert publisher._assess_confidence(TRANSLATION_TYPE_AGGREGATION_BUILT) == CONFIDENCE_HIGH

    def test_confidence_medium_for_dax_translated(self):
        """DAX_TRANSLATED gets MEDIUM confidence."""
        publisher = DatabricksPublisher(_cfg())
        assert publisher._assess_confidence(TRANSLATION_TYPE_DAX_TRANSLATED) == CONFIDENCE_MEDIUM

    def test_confidence_none_for_dax_skipped(self):
        """DAX_SKIPPED gets NONE confidence."""
        publisher = DatabricksPublisher(_cfg())
        assert publisher._assess_confidence(TRANSLATION_TYPE_DAX_SKIPPED) == CONFIDENCE_NONE

    def test_metric_view_groups_measures_by_dataset(self):
        """Metric views group all measures per dataset into one view."""
        model = SMLModel(
            unique_name="multi",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="AMOUNT", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="QTY", data_type=DataType.INTEGER),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="total_amount",
                    dataset="Sales",
                    source_column="AMOUNT",
                    aggregation=AggregationType.SUM,
                ),
                SMLMetric(
                    unique_name="total_qty",
                    dataset="Sales",
                    source_column="QTY",
                    aggregation=AggregationType.SUM,
                ),
            ],
        )
        publisher = DatabricksPublisher(_cfg())

        stmts, created, _, _ = publisher.generate_measure_view_statements(
            model, view_type_override="metric_view"
        )

        # Should be ONE metric view with both measures
        assert created == 1
        assert "total_amount" in stmts[0]
        assert "total_qty" in stmts[0]
