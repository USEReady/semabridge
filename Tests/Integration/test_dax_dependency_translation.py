from types import SimpleNamespace

from semabridge.converter.dax_translator import DAXTranslator


def _metric(name: str, expr: str, dataset: str = "Fact", sql: str | None = None):
    return SimpleNamespace(
        unique_name=name,
        expression=expr,
        dataset=dataset,
        sql_expression=sql,
    )


def test_category2_dependency_replacement_arithmetic():
    translator = DAXTranslator()
    metrics = [
        _metric("Total Revenue", "SUM([Revenue])"),
        _metric("Total COGS", "SUM([COGS])"),
        _metric("Gross Margin", "[Total Revenue] - [Total COGS]"),
    ]

    sql = translator._try_branching("[Total Revenue] - [Total COGS]", metrics)

    assert sql is not None
    assert 'SUM(fact."REVENUE")' in sql
    assert 'SUM(fact."COGS")' in sql
    assert " - " in sql


def test_category2_divide_safe_division_default_zero():
    translator = DAXTranslator()
    metrics = [
        _metric("Total Revenue", "SUM([Revenue])"),
        _metric("Total COGS", "SUM([COGS])"),
        _metric("Gross Margin", "[Total Revenue] - [Total COGS]"),
    ]

    sql = translator._try_branching("DIVIDE([Gross Margin], [Total Revenue])", metrics)

    assert sql is not None
    assert "COALESCE(" in sql
    assert "NULLIF(" in sql
    assert ", 0)" in sql


def test_category3_totalytd_metadata_injection():
    translator = DAXTranslator()
    metrics = [
        _metric("Total Revenue", "SUM([Revenue])"),
    ]

    result = translator.translate(
        "TOTALYTD([Total Revenue], 'Calendar'[Date])",
        table_alias="FACT",
        dataset_name="TestDataset",
        metrics_context=metrics,
    )

    # Snowflake semantic-view METRICS clauses do not support window
    # functions (OVER) — TOTALYTD must translate to a scalar CASE WHEN
    # aggregate bounded by the fact table's MAX_DATE anchor instead.
    assert result.is_success
    assert result.sql is not None
    assert "OVER (" not in result.sql
    assert "CASE WHEN" in result.sql
    assert "DATE_TRUNC('YEAR'" in result.sql
    assert 'FACT."REVENUE"' in result.sql
    assert 'FACT."MAX_DATE"' in result.sql


def test_category3_totalytd_with_inner_sum_wrapper():
    translator = DAXTranslator()

    result = translator.translate(
        "TOTALYTD(SUM([Revenue]), 'Calendar'[Date])",
        table_alias="FACT",
        dataset_name="TestDataset",
    )

    assert result.is_success
    assert result.sql is not None
    assert "OVER (" not in result.sql
    assert "CASE WHEN" in result.sql
    assert "DATE_TRUNC('YEAR'" in result.sql
    assert 'FACT."REVENUE"' in result.sql
    assert "SUM(SUM(" not in result.sql


def test_category2_static_filter_string_and_numeric_equality():
    translator = DAXTranslator()

    string_result = translator.translate(
        'CALCULATE(SUM(FACT[Revenue]), \'Scenario\'[Scenario] = "Budget")',
        table_alias="FACT",
        dataset_name="TestDataset",
    )
    numeric_result = translator.translate(
        "CALCULATE(SUM(FACT[Revenue]), 'Scenario'[ScenarioId] = 3)",
        table_alias="FACT",
        dataset_name="TestDataset",
    )

    assert string_result.is_success
    assert string_result.sql is not None
    assert "CASE WHEN" in string_result.sql
    assert "SCENARIO.SCENARIO = 'Budget'" in string_result.sql
    assert 'SUM(CASE WHEN SCENARIO.SCENARIO = \'Budget\' THEN FACT."REVENUE" ELSE 0 END)' == string_result.sql

    assert numeric_result.is_success
    assert numeric_result.sql is not None
    assert "SCENARIO.SCENARIOID = 3" in numeric_result.sql
    assert 'SUM(CASE WHEN SCENARIO.SCENARIOID = 3 THEN FACT."REVENUE" ELSE 0 END)' == numeric_result.sql


def test_category2_static_filter_with_filter_wrapper_spacing_variants():
    translator = DAXTranslator()

    wrapped_result = translator.translate(
        'CALCULATE([Total Revenue], FILTER(Scenario, Scenario[Scenario] = "Budget"))',
        table_alias="FACT",
        dataset_name="TestDataset",
        metrics_context=[_metric("Total Revenue", "SUM([Revenue])")],
    )
    compact_wrapped_result = translator.translate(
        'CALCULATE([Total Revenue], FILTER(Scenario,Scenario[Scenario]="Budget"))',
        table_alias="FACT",
        dataset_name="TestDataset",
        metrics_context=[_metric("Total Revenue", "SUM([Revenue])")],
    )

    assert wrapped_result.is_success
    assert wrapped_result.sql is not None
    assert "CASE WHEN SCENARIO.SCENARIO = 'Budget'" in wrapped_result.sql
    assert compact_wrapped_result.is_success
    assert compact_wrapped_result.sql is not None
    assert "CASE WHEN SCENARIO.SCENARIO = 'Budget'" in compact_wrapped_result.sql


def test_no_partial_dax_sql_emission_for_calculate_sameperiodlastyear():
    translator = DAXTranslator()
    metrics = [
        _metric("Total Revenue", "SUM([Revenue])"),
        _metric(
            "Revenue SPLY",
            "CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))",
        ),
    ]

    sql = translator._try_branching(
        "CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))",
        metrics,
    )

    assert sql is None


def test_translate_resolves_previously_unsupported_time_offsets():
    # SAMEPERIODLASTYEAR (and DATEADD/DATESYTD/PARALLELPERIOD/etc.) used to
    # be rejected outright before any translation tier ran at all. They now
    # get a fair shot at the AST renderer's correct CASE-WHEN-bounded
    # translation instead.
    translator = DAXTranslator()
    metrics = [
        _metric("Total Revenue", "SUM([Revenue])"),
    ]

    result = translator.translate(
        "CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('Calendar'[Date]))",
        table_alias="FACT",
        dataset_name="TestDataset",
        metrics_context=metrics,
    )

    assert result.is_success
    assert result.sql is not None
    assert "OVER (" not in result.sql
    assert "CASE WHEN" in result.sql
    assert 'FACT."REVENUE"' in result.sql
    assert "SUM(SUM(" not in result.sql


def test_nested_totalytd_static_filter_dependency_chain_resolves():
    translator = DAXTranslator()
    metrics = [
        _metric("Revenue Budget", 'CALCULATE(SUM([Revenue]), \'Scenario\'[Scenario] = "Budget")'),
        _metric("RevenueTY", 'CALCULATE(SUM([Revenue]), \'Scenario\'[Scenario] = "Actual")'),
        _metric("Revenue Var to Budget", '[RevenueTY] - [Revenue Budget]'),
        _metric("YTD Revenue", "TOTALYTD([RevenueTY], 'Calendar'[Date])"),
        _metric("YTD COGS", "TOTALYTD([Revenue Budget], 'Calendar'[Date])"),
    ]

    ytd_result = translator.translate(
        "TOTALYTD([RevenueTY], 'Calendar'[Date])",
        table_alias="FACT",
        dataset_name="TestDataset",
        metrics_context=metrics,
    )
    budget_result = translator.translate(
        'CALCULATE(SUM([Revenue]), \'Scenario\'[Scenario] = "Budget")',
        table_alias="FACT",
        dataset_name="TestDataset",
        metrics_context=metrics,
    )
    variance_result = translator.translate(
        '[RevenueTY] - [Revenue Budget]',
        table_alias="FACT",
        dataset_name="TestDataset",
        metrics_context=metrics,
    )

    assert ytd_result.is_success
    assert ytd_result.sql is not None
    assert "OVER (" not in ytd_result.sql
    assert "CASE WHEN" in ytd_result.sql
    assert "DATE_TRUNC('YEAR'" in ytd_result.sql
    assert "SUM(SUM(" not in ytd_result.sql

    assert budget_result.is_success
    assert budget_result.sql is not None
    assert "CASE WHEN SCENARIO.SCENARIO = 'Budget'" in budget_result.sql

    assert variance_result.is_success
    assert variance_result.sql is not None
    assert "-" in variance_result.sql
    assert "CASE WHEN SCENARIO.SCENARIO = 'Budget'" in variance_result.sql


def test_totalytd_measure_dependency_chain_avoids_nested_aggregates():
    translator = DAXTranslator()
    metrics = [
        _metric("Revenue Budget", 'CALCULATE(SUM([Revenue]), \'Scenario\'[Scenario] = "Budget")'),
        _metric("RevenueTY", 'CALCULATE(SUM([Revenue]), \'Scenario\'[Scenario] = "Actual")'),
        _metric("Revenue Var to Budget", '[RevenueTY] - [Revenue Budget]'),
    ]

    revenue_ty_ytd = translator.translate(
        "TOTALYTD([RevenueTY], 'Calendar'[Date])",
        table_alias="FACT",
        dataset_name="TestDataset",
        metrics_context=metrics,
    )
    variance_ytd = translator.translate(
        "TOTALYTD([Revenue Var to Budget], 'Calendar'[Date])",
        table_alias="FACT",
        dataset_name="TestDataset",
        metrics_context=metrics,
    )

    assert revenue_ty_ytd.is_success
    assert revenue_ty_ytd.sql is not None
    assert "SUM(SUM(" not in revenue_ty_ytd.sql
    assert "OVER (" not in revenue_ty_ytd.sql
    assert "CASE WHEN" in revenue_ty_ytd.sql

    assert variance_ytd.is_success
    assert variance_ytd.sql is not None
    assert "SUM(SUM(" not in variance_ytd.sql
    assert "OVER (" not in variance_ytd.sql
    assert "CASE WHEN" in variance_ytd.sql
    assert " - " in variance_ytd.sql


def test_no_partial_dax_sql_emission_for_if_blank():
    translator = DAXTranslator()
    metrics = [
        _metric("Total Revenue", "SUM([Revenue])"),
        _metric("Total COGS", "SUM([COGS])"),
    ]

    sql = translator._try_branching(
        "IF([Total Revenue], [Total Revenue]-[Total COGS], BLANK())",
        metrics,
    )

    assert sql is None
