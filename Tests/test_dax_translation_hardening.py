import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from semabridge.converter.dax_engine import DaxTranslationEngine, sanitize_llm_sql
from semabridge.converter.llm_dax_translator import LLMDAXTranslator
from semabridge.converter.dax_rule_translator import rule_based_translation
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.translator import MetricExpressionTranslator


def test_sanitize_llm_sql_removes_var_and_unwraps_select():
    sql = """
VAR total = SUM(Sales[Amount])
SELECT SUM(CASE WHEN region = 'North' THEN amount ELSE 0 END) FROM sales
"""

    sanitized = sanitize_llm_sql(sql)

    assert "VAR" not in sanitized.upper()
    assert not sanitized.upper().startswith("SELECT")
    assert sanitized == "SUM(CASE WHEN region = 'North' THEN amount ELSE 0 END)"


def test_sanitize_llm_sql_ignores_parentheses_inside_string_literals():
    sql = "CAST('Inventory (Excluding LIFO Reserves...' AS STRING)"

    sanitized = sanitize_llm_sql(sql)

    assert sanitized == sql
    assert not sanitized.endswith("))")


def test_llm_prompt_enforces_anchor_rules():
    translator = LLMDAXTranslator()

    prompt = translator._build_prompt(
        dax="CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)",
        table_alias="corporate_dsi_aggregate",
        dataset_name="Inventory Semantic Model",
        metric_name="Corporate IOH",
        schema_context={
            "dates": ["fiscal_yr_period", "cal_dt"],
            "corporate_dsi_aggregate": ["ioh_excldng_lifo_amt", "_current_fiscal_period", "max_date"],
        },
    )

    assert "Nested aggregates are FORBIDDEN" in prompt
    assert "_current_fiscal_period" in prompt
    assert "max_date" in prompt
    assert "Corporate DSI style measures" in prompt


def test_llm_validation_rejects_nested_aggregates():
    translator = LLMDAXTranslator()

    is_valid, error = translator._validate_sql(
        "SUM(CASE WHEN dates.fiscal_yr_period < MAX(_current_fiscal_period) THEN amount ELSE 0 END)",
        original_dax="CALCULATE(SUM(Sales[Amount]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)",
        table_alias="sales",
    )

    assert not is_valid
    assert "Nested aggregate function detected" in error


def test_llm_validation_allows_scalar_time_wrapper():
    translator = LLMDAXTranslator()

    is_valid, error = translator._validate_sql(
        "MAX(current_date())",
        original_dax="TODAY()",
        table_alias="sales",
    )

    assert is_valid
    assert error == ""


def test_llm_parse_response_auto_fixes_bare_current_date():
    translator = LLMDAXTranslator()

    result = translator._parse_response(
        "CURRENT_DATE",
        original_dax="TODAY()",
        table_alias="sales",
        metric_name="Today",
    )

    assert result.is_valid
    assert result.sql == "MAX(current_date())"


def test_llm_validation_rejects_subquery_and_unsafe_division():
    translator = LLMDAXTranslator()

    subquery_valid, subquery_error = translator._validate_sql(
        "SUM(CASE WHEN x > (SELECT MAX(y) FROM t) THEN 1 ELSE 0 END)",
        original_dax="CALCULATE(SUM(X[Amount]), X[Flag] = 1)",
        table_alias="x",
        metric_name="",
    )
    division_valid, division_error = translator._validate_sql(
        "SUM(amount) / SUM(cost)",
        original_dax="DIVIDE([Amount], [Cost])",
        table_alias="sales",
        metric_name="",
    )

    assert not subquery_valid
    assert "Subquery detected" in subquery_error
    assert not division_valid
    assert "requires NULLIF" in division_error


def test_llm_business_rules_cover_dsi_and_share_metrics():
    translator = LLMDAXTranslator()

    dsi_valid, dsi_error = translator._validate_sql(
        "SUM(CASE WHEN flag = 1 THEN amount ELSE 0 END)",
        original_dax="[Corporate IOH]",
        table_alias="sales",
        metric_name="Corporate DSI Monthly",
    )
    share_valid, share_error = translator._validate_sql(
        "COALESCE(SUM(a) / NULLIF(SUM(b), 0), 0)",
        original_dax="[Market Share]",
        table_alias="sales",
        metric_name="Market Share",
    )

    assert not dsi_valid
    assert "DSI measure must reference a DSI-related source column" in dsi_error
    assert share_valid
    assert share_error == ""


def test_llm_validation_accepts_reference_metric_view_sql_forms():
    translator = LLMDAXTranslator()

    accepted_cases = [
        "SUM(`sales`.`amount`)",
        "AVG(`sales`.`amount`)",
        "COUNT(DISTINCT `product`.`id`)",
        "COUNT(*)",
        "MAX(current_date())",
        "SUM(CASE WHEN dates.fiscal_yr_period < _current_fiscal_period THEN corporate_dsi_aggregate.ioh_excldng_lifo_amt ELSE 0 END)",
        "SUM(CASE WHEN date_col >= DATE_TRUNC('YEAR', max_date) AND date_col <= max_date THEN amount ELSE 0 END)",
        "SUM(CASE WHEN date_col > DATEADD(month, -12, max_date) AND date_col <= max_date THEN amount ELSE 0 END)",
        "SUM(CASE WHEN date_col >= DATE_TRUNC('YEAR', DATEADD(year, -1, max_date)) AND date_col <= DATEADD(year, -1, max_date) THEN amount ELSE 0 END)",
        "ANY_VALUE(CASE WHEN businessunits.business_unit = 'USP' THEN 'text1' ELSE 'text2' END)",
        "ANY_VALUE(CONCAT('Last Refreshed: ', DATE_FORMAT(table.gl_refresh_datetime, 'MM/dd/yyyy HH:mm:ss')))",
        "ANY_VALUE(CASE WHEN businessunits.business_unit = 'USP' THEN 'bullet text' WHEN businessunits.business_unit = 'MSH' THEN 'other text' ELSE NULL END)",
        "COALESCE(SUM(CASE WHEN product.isvanarsdel = 'Yes' THEN amount ELSE 0 END) / NULLIF(SUM(amount), 0), 0) * 100",
        "COALESCE(SUM(CASE WHEN isvanarsdel = 'Yes' THEN units ELSE 0 END) / NULLIF(SUM(units), 0), 0)",
        "COALESCE( A / NULLIF(B, 0), 0)",
        "COALESCE(sales, 0)",
        "COUNT_IF(`sales`.`amount` IS NULL)",
        "SUM(corporate_dsi_aggregate.ioh_excldng_lifo_amt)",
        "AVG(corporate_dsi_aggregate.dsi_mnthly)",
    ]

    for sql in accepted_cases:
        is_valid, error = translator._validate_sql(
            sql,
            original_dax="placeholder",
            table_alias="sales",
            metric_name="Market Share" if "/" in sql and "share" in sql.lower() else "",
        )
        assert is_valid, sql
        assert error == ""


def test_llm_validation_rejects_reference_forbidden_patterns():
    translator = LLMDAXTranslator()

    forbidden_cases = [
        ("SUMX(FILTER(Sales, Sales[Amount] > 100), Sales[Amount])", "Unsupported DAX construct"),
        ("CALCULATE(SUM(Sales[Amount]), FILTER(Product, Product[Color] = 'Red'))", "Unsupported DAX construct"),
        ("TOTALYTD(SUM(Sales[Amount]), 'Date'[Date], FILTER(ALL(Date), ...))", "Unsupported DAX construct"),
        ("RANKX(ALL(Product), [Sales])", "Unsupported DAX construct"),
        ("SUM(MAX(Sales[Amount]))", "Nested aggregate function detected"),
        ("CURRENT_DATE", "CURRENT_DATE must be wrapped in MAX(current_date())"),
        ("SUM(amount) / SUM(cost)", "Division operator (/) requires NULLIF"),
        ("SUM(CASE WHEN x > (SELECT MAX(y) FROM t) THEN 1 ELSE 0 END)", "Subquery detected"),
        ("FILTER(table, table.col = 1)", "Unsupported DAX construct"),
        ("SELECTEDVALUE(businessunits.business_unit)", "Unsupported DAX construct"),
        ("SWITCH(TRUE(), 1 = 1, 'x')", "Unsupported DAX construct"),
        ("COUNTROWS(sales)", "Unsupported DAX construct"),
        ("COUNTBLANK(sales.amount)", "Unsupported DAX construct"),
    ]

    for sql, expected_error in forbidden_cases:
        is_valid, error = translator._validate_sql(
            sql,
            original_dax="placeholder",
            table_alias="sales",
        )
        assert not is_valid, sql
        assert expected_error in error, (sql, error)


def test_llm_parse_response_matches_actual_failing_measures():
    translator = LLMDAXTranslator()

    cases = [
        (
            "Corporate IOH",
            """
VAR _today = [Today]
VAR fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
            "SUM(CASE WHEN dates.fiscal_yr_period < _current_fiscal_period THEN corporate_dsi_aggregate.ioh_excldng_lifo_amt ELSE 0 END)",
        ),
        (
            "Corporate DSI Monthly",
            """
VAR _today = [Today]
VAR fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[DSI_MNTHLY]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
            "SUM(CASE WHEN dates.fiscal_yr_period < _current_fiscal_period THEN corporate_dsi_aggregate.dsi_mnthly ELSE 0 END)",
        ),
        (
            "Corporate DSI Quarterly",
            """
VAR _today = [Today]
VAR fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[DSI_QTD]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
            "SUM(CASE WHEN dates.fiscal_yr_period < _current_fiscal_period THEN corporate_dsi_aggregate.dsi_qtd ELSE 0 END)",
        ),
        (
            "Corporate DSI Yearly",
            """
VAR _today = [Today]
VAR fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[DSI_YRLY]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
            "SUM(CASE WHEN dates.fiscal_yr_period < _current_fiscal_period THEN corporate_dsi_aggregate.dsi_yrly ELSE 0 END)",
        ),
        (
            "Corporate COS",
            """
VAR _today = [Today]
VAR fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[COS_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
            "SUM(CASE WHEN dates.fiscal_yr_period < _current_fiscal_period THEN corporate_dsi_aggregate.cos_excldng_lifo_amt ELSE 0 END)",
        ),
        (
            "Corporate DSI Last Refreshed",
            "= CONCATENATE(\"Last Refreshed: \", MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))",
            "ANY_VALUE(CONCAT('Last Refreshed: ', DATE_FORMAT(corporate_dsi_last_refreshed.gl_refresh_datetime, 'MM/dd/yyyy HH:mm:ss')))",
        ),
        (
            "Inventory Fact Last Refreshed",
            "= CONCATENATE(\"Last Refreshed: \", MAX('Inventory Fact Last Refreshed'[GL Refresh Datetime]))",
            "ANY_VALUE(CONCAT('Last Refreshed: ', DATE_FORMAT(inventory_fact_last_refreshed.gl_refresh_datetime, 'MM/dd/yyyy HH:mm:ss')))",
        ),
        (
            "Subledger Business Unit Callout",
            """
VAR _sel = SELECTEDVALUE('Business Units'[Business Unit])
VAR _b = UNICHAR(8226)
RETURN SWITCH(TRUE(), ISBLANK(_sel), \"\", _sel = \"USP\", _b & \" Source of dashboard is SAP...\", _sel = \"MSH\", _b & \" Source...\", \"\")
""",
            "ANY_VALUE(CASE WHEN businessunits.business_unit = 'USP' THEN '• Source of dashboard is SAP...' WHEN businessunits.business_unit = 'MSH' THEN '• Source...' ELSE NULL END)",
        ),
        (
            "GL Business Unit Callout",
            """
VAR _sel = SELECTEDVALUE('Business Units'[Business Unit])
RETURN SWITCH(TRUE(), ISBLANK(_sel), \"\", _sel = \"USP\", \"• GL data sourced from GRC (Oracle)\", _sel = \"MSH\", \"• GL data sourced from GRC (Oracle)\", \"\")
""",
            "ANY_VALUE(CASE WHEN businessunits.business_unit = 'USP' THEN '• GL data sourced from GRC (Oracle)' WHEN businessunits.business_unit = 'MSH' THEN '• GL data sourced from GRC (Oracle)' ELSE NULL END)",
        ),
        (
            "DSI Calculation Callout",
            """
VAR _nl = UNICHAR(10)
VAR _b = UNICHAR(8226)
RETURN
    _b & \" Inventory excluding LIFO & Reserve...\" & _nl &
    _b & \" Cost of Sales excluding LIFO...\" & _nl &
    _b & \" DSI (Monthly) = ...\"
""",
            "ANY_VALUE(CONCAT('• Inventory excluding LIFO & Reserve...', CHAR(10), '• Cost of Sales excluding LIFO...', CHAR(10), '• DSI (Monthly) = ...'))",
        ),
    ]

    for metric_name, dax, expected_sql in cases:
        result = translator._parse_response(
            expected_sql,
            original_dax=dax,
            table_alias="fact",
            metric_name=metric_name,
        )

        assert result.is_valid, metric_name
        assert result.sql == expected_sql


def test_llm_rejects_actual_forbidden_dax_shapes_in_expected_sql():
    translator = LLMDAXTranslator()

    forbidden_sql_cases = [
        ("SUM(MAX(corporate_dsi_aggregate.ioh_excldng_lifo_amt))", "Nested aggregate function detected"),
        ("SUM(CASE WHEN x > (SELECT MAX(y) FROM t) THEN 1 ELSE 0 END)", "Subquery detected"),
        ("SUM(amount) / SUM(cost)", "Division operator (/) requires NULLIF to prevent division by zero"),
        ("SUM(CASE WHEN x = 1 THEN amount FILTER (WHERE y = 2) ELSE 0 END)", "FILTER clause is not allowed inside aggregates"),
        ("SUMX(FILTER(Sales, Sales[Amount] > 100), Sales[Amount])", "Unsupported DAX construct detected in SQL output"),
        ("CURRENT_DATE", "CURRENT_DATE must be wrapped in MAX(current_date())"),
    ]

    for sql, expected_error in forbidden_sql_cases:
        is_valid, error = translator._validate_sql(
            sql,
            original_dax="placeholder",
            table_alias="sales",
            metric_name="",
        )

        assert not is_valid, sql
        assert expected_error in error, (sql, error)


def test_llm_additional_real_world_cases_cover_time_iterators_and_filters():
    translator = LLMDAXTranslator()

    accepted_cases = [
        (
            "CALCULATE(SUM(Sales[Amount]), Product[Color] = \"Red\", Customer[City] = \"London\")",
            "SUM(CASE WHEN product.color = 'Red' AND customer.city = 'London' THEN amount ELSE 0 END)",
        ),
        (
            "CALCULATE(SUM(Sales[Amount]), Product[Color] = \"Red\" OR Product[Color] = \"Blue\")",
            "SUM(CASE WHEN product.color IN ('Red', 'Blue') THEN amount ELSE 0 END)",
        ),
        (
            "CALCULATE(COUNTROWS('Sales'), Sales[Amount] > 0)",
            "COUNT(CASE WHEN amount > 0 THEN 1 ELSE NULL END)",
        ),
        (
            "CALCULATE(CALCULATE(SUM(Sales[Amount]), Product[Color] = \"Red\"), Customer[City] = \"London\")",
            "SUM(CASE WHEN product.color = 'Red' AND customer.city = 'London' THEN amount ELSE 0 END)",
        ),
        (
            "SELECTEDVALUE(Product[Name])",
            "ANY_VALUE(product.name)",
        ),
        (
            "IF(ISBLANK(Product[Name]), \"Unknown\", Product[Name])",
            "COALESCE(product.name, 'Unknown')",
        ),
        (
            "CONCATENATE(\"Line1\", UNICHAR(10), \"Line2\")",
            "CONCAT('Line1', CHAR(10), 'Line2')",
        ),
        (
            "DIVIDE([A], [B], 0)",
            "COALESCE( A / NULLIF(B, 0), 0)",
        ),
    ]

    for dax, expected_sql in accepted_cases:
        result = translator._parse_response(
            expected_sql,
            original_dax=dax,
            table_alias="sales",
            metric_name="test",
        )

        assert result.is_valid, dax
        assert result.sql == expected_sql


def test_llm_additional_real_world_cases_reject_time_intelligence_iterators_and_all_filters():
    translator = LLMDAXTranslator()

    rejected_cases = [
        "CALCULATE(SUM(Sales[Amount]), SAMEPERIODLASTYEAR('Date'[Date]))",
        "CALCULATE(SUM(Sales[Amount]), DATEADD('Date'[Date], -1, YEAR))",
        "SUMX(FILTER(Sales, Sales[Amount] > 100), Sales[Amount])",
        "AVERAGEX(VALUES(Product[Category]), [Sales])",
        "CALCULATE(SUM(Sales[Amount]), ALL(Product))",
        "CALCULATE(SUM(Sales[Amount]), ALLEXCEPT(Sales, Sales[Region]))",
        "VAR MinDate = MIN('Date'[Date]) RETURN CALCULATE(SUM(Sales[Amount]), 'Date'[Date] > MinDate)",
        "CALCULATE(DISTINCTCOUNT(Product[ID]), FILTER(Product, Product[Category] = \"Electronics\"))",
        "FIRSTNONBLANK('Product'[Name], [Sales])",
    ]

    for dax in rejected_cases:
        is_valid, error = translator._validate_sql(
            dax,
            original_dax=dax,
            table_alias="sales",
            metric_name="",
        )

        assert not is_valid, dax
        assert error, dax


def test_snowflake_metric_expression_normalizes_case_cast_inside_sum():
    expr = (
        "SUM(CASE WHEN CORPORATE_DSI_AGGREGATE.FISCAL_YR_PERIOD < "
        "CORPORATE_DSI_AGGREGATE._CURRENT_FISCAL_PERIOD THEN "
        "CORPORATE_DSI_AGGREGATE.COS_EXCLDNG_LIFO_AMT ELSE 0 END::FLOAT)"
    )

    normalized = MetricsClauseBuilder._normalize_snowflake_metric_expression(expr)

    assert normalized == (
        "SUM(CAST((CASE WHEN CORPORATE_DSI_AGGREGATE.FISCAL_YR_PERIOD < "
        "CORPORATE_DSI_AGGREGATE._CURRENT_FISCAL_PERIOD THEN "
        "CORPORATE_DSI_AGGREGATE.COS_EXCLDNG_LIFO_AMT ELSE 0 END) AS FLOAT))"
    )


def test_safe_sum_uses_cast_for_case_expression():
    translator = MetricExpressionTranslator()

    sql = translator._build_safe_sum_sql("CASE WHEN FLAG THEN AMOUNT ELSE 0 END")

    assert sql == "SUM(CAST((CASE WHEN FLAG THEN AMOUNT ELSE 0 END) AS FLOAT))"


def test_fiscal_var_calculate_translates_without_llm_keywords():
    dax = """
VAR _today = [Today]
VAR fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
"""

    sql = rule_based_translation(dax, table_alias="corporate_dsi_aggregate")

    assert sql is not None
    assert "VAR" not in sql.upper()
    assert "SELECT" not in sql.upper()
    assert "_current_fiscal_period" in sql
    assert "IOH_EXCLDNG_LIFO_AMT" in sql


def test_fiscal_var_calculate_ignores_quoted_dates_lookup():
    dax = """
VAR _today = [Today]
VAR fiscalMonth = CALCULATE(MAX('Dates'[FISCAL_YR_PERIOD]), 'Dates'[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), 'Dates'[FISCAL_YR_PERIOD] < fiscalMonth)
"""

    sql = rule_based_translation(dax, table_alias="corporate_dsi_aggregate")

    assert sql is not None
    assert "IOH_EXCLDNG_LIFO_AMT" in sql
    assert "MAX(CASE" not in sql.upper()
    assert "FISCAL_YR_PERIOD THEN" not in sql.upper()


def test_engine_sanitizes_llm_fallback_output():
    engine = DaxTranslationEngine(cache_enabled=False)

    sql, metrics = engine.translate(
        "USERELATIONSHIP(Sales[ShipDate], Date[Date])",
        table_alias="sales",
        target_dialect="snowflake",
    )

    assert sql is not None
    assert not sql.upper().startswith("SELECT")
    assert "SELECT" not in sql.upper()
    assert "FROM" not in sql.upper()
    assert "VAR" not in sql.upper()


def test_snowflake_fixers_compat_module_imports():
    from semabridge.connectors.snowflake_emitter_parts import fixers

    assert callable(fixers.fix_global_sums)
    assert callable(fixers.prune_unresolved_metric_lines)


def test_inventory_project_measures_translate_for_snowflake():
    measures = {
        "Today": "TODAY()",
        "Corporate IOH": """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
        "Corporate DSI Monthly": """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[DSI_MNTHLY]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
        "Corporate DSI Quarterly": """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[DSI_QTD]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
        "Corporate DSI Yearly": """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[DSI_YRLY]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
        "Corporate COS": """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[COS_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""",
        "Source Value Total Stock": "SUM('Inventory Fact'[Source Value Total Stock])",
        "Corporate DSI Last Refreshed": (
            'CONCATENATE("Last Refreshed: ", '
            "MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))"
        ),
        "Inventory Fact Last Refreshed": (
            'CONCATENATE("Last Refreshed: ", '
            "MAX('Inventory Fact Last Refreshed'[GL Refresh Datetime]))"
        ),
        "Subledger Business Unit Callout": """
VAR _sel = SELECTEDVALUE ( 'Business Units'[Business Unit] )
VAR _nl = UNICHAR(10)
VAR _b  = UNICHAR(8226)
RETURN
SWITCH (
    TRUE(),
    ISBLANK ( _sel ), "",
    _sel = "USP", _b & " Source of dashboard is SAP subledger via BW and inventory dollars are valued at MAC to tie to the general ledger",
    _sel = "MSH", _b & " Source of dashboard is SAP subledger via BW and inventory dollars are valued at WAC",
    _sel = "CMM", _b & " Source of dashboard is SAP subledger via BW and inventory dollars are valued at WAC",
    ""
)
""",
        "GL Business Unit Callout": """
VAR _sel = SELECTEDVALUE ( 'Business Units'[Business Unit] )
VAR _nl = UNICHAR(10)
VAR _b  = UNICHAR(8226)
RETURN
SWITCH (
    TRUE(),
    ISBLANK ( _sel ), "",  
    _sel = "USP", _b & " Source of dashboard is general ledger and total net inventory dollars are valued at MAC",
    _sel = "MSH", _b & " Source of dashboard is general ledger and total net inventory dollars are valued at WAC",
    _sel = "CMM", _b & " Source of dashboard is general ledger and total net inventory dollars are valued at WAC",
    ""
)
""",
        "DSI Calculation Callout": """
VAR _nl = UNICHAR(10)
VAR _b  = UNICHAR(8226)
RETURN
    _b & " Inventory excluding LIFO & Reserve - '130000 - Inventories, Net' (Excluding '138000 - LIFO Reserves')" & _nl &
    _b & " Cost of Sales excluding LIFO - '500000 - Cost of Sales' (Excluding '569520 - LIFO Provision - GAAP Only')" & _nl &
    _b & " DSI (Monthly) = Inventory excluding LIFO & Reserve/(Cost of Sales excluding LIFO/30)"
""",
        "WAC Value Total Stock": "SUM('Inventory Fact'[WAC Value Total Stock])",
    }
    expected_tokens = {
        "Corporate IOH": "IOH_EXCLDNG_LIFO_AMT",
        "Corporate DSI Monthly": "DSI_MNTHLY",
        "Corporate DSI Quarterly": "DSI_QTD",
        "Corporate DSI Yearly": "DSI_YRLY",
        "Corporate COS": "COS_EXCLDNG_LIFO_AMT",
        "Source Value Total Stock": "SOURCE_VALUE_TOTAL_STOCK",
        "WAC Value Total Stock": "WAC_VALUE_TOTAL_STOCK",
    }

    engine = DaxTranslationEngine(cache_enabled=False)
    translated = {}

    for name, dax in measures.items():
        sql, metrics = engine.translate(
            dax,
            table_alias="fact",
            metric_name=name,
            target_dialect="snowflake",
        )
        translated[name] = sql
        sql_upper = (sql or "").upper()

        assert sql, name
        assert "VAR" not in sql_upper, name
        assert not sql_upper.strip().startswith("SELECT"), name
        assert "CAST(NULL AS DOUBLE)" not in sql_upper, name
        assert sql_upper.strip() != "NULL", name
        assert metrics.strategy.value in {"rule_based", "ast_based", "llm_fallback"}

    assert len(translated) == 13
    for name in (
        "Corporate IOH",
        "Corporate DSI Monthly",
        "Corporate DSI Quarterly",
        "Corporate DSI Yearly",
        "Corporate COS",
    ):
        assert "_current_fiscal_period" in translated[name]

    for name, token in expected_tokens.items():
        assert token in translated[name].upper()


def test_snowflake_semantic_ddl_adds_current_fiscal_period_source_view():
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.sml.models import (
        AggregationType,
        DataType,
        SMLColumn,
        SMLDataset,
        SMLMetric,
        SMLModel,
    )

    fiscal_dax = """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
"""
    model = SMLModel(
        unique_name="Inventory Semantic Model",
        datasets=[
            SMLDataset(unique_name="Project Measures", source_table="Project Measures"),
            SMLDataset(
                unique_name="Corporate DSI Aggregate",
                source_table="Corporate DSI Aggregate",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="FISCAL_YR_PERIOD", data_type=DataType.STRING),
                    SMLColumn(unique_name="IOH_EXCLDNG_LIFO_AMT", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Corporate IOH",
                dataset="Project Measures",
                expression=fiscal_dax,
                aggregation=AggregationType.NONE,
            )
        ],
    )
    emitter = SnowflakeEmitter(
        SnowflakeConfig(
            account="dummy",
            user="dummy",
            password="dummy",
            warehouse="WH",
            database="DB",
            schema_name="PUBLIC",
        )
    )

    ddls = emitter.semantic_view_builder.generate_ddls(model)
    joined = "\n".join(ddls).upper()

    assert "CREATE OR REPLACE VIEW" in joined
    assert "CORPORATE_DSI_AGGREGATE_SEMABRIDGE_FISCAL" in joined
    assert '"_CURRENT_FISCAL_PERIOD"' in joined
    assert "CORPORATE_DSI_AGGREGATE._CURRENT_FISCAL_PERIOD" in joined
    assert "CAST(NULL AS DOUBLE)" not in joined
    assert " VAR " not in joined


def test_snowflake_ignores_bad_stored_sql_expression_and_translates_dax():
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.sml.models import (
        AggregationType,
        DataType,
        SMLColumn,
        SMLDataset,
        SMLMetric,
        SMLModel,
    )

    fiscal_dax = """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX('Dates'[FISCAL_YR_PERIOD]), 'Dates'[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), 'Dates'[FISCAL_YR_PERIOD] < fiscalMonth)
"""
    model = SMLModel(
        unique_name="Inventory Semantic Model",
        datasets=[
            SMLDataset(unique_name="Project Measures", source_table="Project Measures"),
            SMLDataset(
                unique_name="Corporate DSI Aggregate",
                source_table="Corporate DSI Aggregate",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="FISCAL_YR_PERIOD", data_type=DataType.STRING),
                    SMLColumn(unique_name="IOH_EXCLDNG_LIFO_AMT", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Corporate IOH",
                dataset="Project Measures",
                expression=fiscal_dax,
                sql_expression="WITH ctx_lvl_1 AS (SELECT * FROM fact WHERE 1=1) SELECT None FROM ctx_lvl_1",
                aggregation=AggregationType.NONE,
            )
        ],
    )
    emitter = SnowflakeEmitter(
        SnowflakeConfig(
            account="dummy",
            user="dummy",
            password="dummy",
            warehouse="WH",
            database="DB",
            schema_name="PUBLIC",
        )
    )

    ddls = emitter.semantic_view_builder.generate_ddls(model)
    joined = "\n".join(ddls).upper()

    assert "IOH_EXCLDNG_LIFO_AMT" in joined
    assert "WITH CTX_LVL_1" not in joined
    assert "SELECT NONE" not in joined
    assert 'CORPORATE_DSI_AGGREGATE."CORPORATE_IOH" AS 0' not in joined


def test_snowflake_falls_back_for_bare_metric_references_in_sql_expression():
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.sml.models import (
        AggregationType,
        DataType,
        SMLColumn,
        SMLDataset,
        SMLMetric,
        SMLModel,
    )

    model = SMLModel(
        unique_name="Competitive Marketing Analysis",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SalesFact",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="ProductID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Units", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(
                unique_name="Sentiment",
                source_table="Sentiment",
                columns=[
                    SMLColumn(unique_name="DateID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Score", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Sentiment",
                dataset="Sentiment",
                source_column="Score",
                aggregation=AggregationType.AVG,
            ),
            SMLMetric(
                unique_name="@Indicator04",
                dataset="SalesFact",
                sql_expression='CASE WHEN "SENTIMENT" < 65 THEN 1 ELSE 2 END',
                aggregation=AggregationType.NONE,
            ),
        ],
    )
    emitter = SnowflakeEmitter(
        SnowflakeConfig(
            account="dummy",
            user="dummy",
            password="dummy",
            warehouse="WH",
            database="DB",
            schema_name="PUBLIC",
        )
    )

    ddls = emitter.semantic_view_builder.generate_ddls(model)
    joined = "\n".join(ddls).upper()

    assert 'SALESFACT."INDICATOR04" AS 0' in joined
    assert 'CASE WHEN "SENTIMENT"' not in joined


def test_snowflake_falls_back_for_dax_leakage_in_sql_expression():
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.sml.models import (
        AggregationType,
        DataType,
        SMLColumn,
        SMLDataset,
        SMLMetric,
        SMLModel,
    )

    leaked_dax_sql = (
        'CASE WHEN CALCULATE(["SENTIMENT"], "MANUFACTURER"[MfgisVanArsdel]="No"))'
        '||ISBLANK(CALCULATE(["SENTIMENT"], "MANUFACTURER"[MfgisVanArsdel]="Yes") '
        'IS NULL THEN BLANK() ELSE 1 END'
    )
    model = SMLModel(
        unique_name="Competitive Marketing Analysis",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SalesFact",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="ProductID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Units", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Sentiment Gap",
                dataset="SalesFact",
                expression=(
                    'IF(ISBLANK(CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]="No"))'
                    '||ISBLANK(CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]="Yes")), '
                    'BLANK(), 1)'
                ),
                sql_expression=leaked_dax_sql,
                aggregation=AggregationType.NONE,
            ),
        ],
    )
    emitter = SnowflakeEmitter(
        SnowflakeConfig(
            account="dummy",
            user="dummy",
            password="dummy",
            warehouse="WH",
            database="DB",
            schema_name="PUBLIC",
        )
    )

    ddls = emitter.semantic_view_builder.generate_ddls(model)
    joined = "\n".join(ddls).upper()

    assert 'SALESFACT."SENTIMENT_GAP" AS 0' in joined
    assert "CALCULATE(" not in joined
    assert "ISBLANK(" not in joined
    assert "||" not in joined


def test_snowflake_translates_leaked_divide_in_sql_expression():
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.sml.models import (
        AggregationType,
        DataType,
        SMLColumn,
        SMLDataset,
        SMLMetric,
        SMLModel,
    )

    model = SMLModel(
        unique_name="Customer Profitability",
        datasets=[
            SMLDataset(
                unique_name="Fact",
                source_table="Fact",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="Profit", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Profit Margin",
                dataset="Fact",
                expression="DIVIDE(SUM('Fact'[Profit]), SUM('Fact'[Revenue]), 0)",
                sql_expression="DIVIDE(SUM('Fact'[Profit]), SUM('Fact'[Revenue]), 0)",
                aggregation=AggregationType.NONE,
            ),
        ],
    )
    emitter = SnowflakeEmitter(
        SnowflakeConfig(
            account="dummy",
            user="dummy",
            password="dummy",
            warehouse="WH",
            database="DB",
            schema_name="PUBLIC",
        )
    )

    ddls = emitter.semantic_view_builder.generate_ddls(model)
    joined = "\n".join(ddls).upper()

    assert "DIVIDE(" not in joined
    assert "COALESCE(" in joined
    assert "NULLIF(" in joined
    assert 'FACT."PROFIT"' in joined
    assert 'FACT."REVENUE"' in joined


def test_snowflake_blocks_untranslated_divide_from_semantic_ddl():
    from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder

    assert MetricsClauseBuilder._contains_unsupported_dax_keywords(
        "DIVIDE([Profit], [Revenue], 0)"
    )


def test_common_llm_translator_sanitizes_provider_select_and_var_output():
    from semabridge.converter.common_dax_translator import (
        CommonDAXTranslator,
        LLMProviderConfig,
        LLMProviderName,
        SQLDialect,
    )

    class FakeProvider:
        config = LLMProviderConfig(
            name=LLMProviderName.GEMINI,
            model="fake",
            api_key_env="NONE",
        )

        def generate(self, prompt: str, timeout_seconds: int) -> str:
            return """
VAR x = SUM(Sales[Amount])
SELECT SUM(CASE WHEN region = 'North' THEN amount ELSE 0 END) FROM sales
"""

    translator = CommonDAXTranslator(
        dialect=SQLDialect.SNOWFLAKE,
        cache_enabled=False,
        providers=[FakeProvider()],
    )

    result = translator.translate(
        dax="CALCULATE(SUM('Sales'[Amount]), 'Sales'[Region] = \"North\")",
        metric_name="North Sales",
        dataset_name="Sales",
        table_alias="sales",
        schema_context={"Sales": ["Amount", "Region"]},
    )

    assert result.is_valid
    assert result.sql == "SUM(CASE WHEN region = 'North' THEN amount ELSE 0 END)"
    assert "VAR" not in result.sql.upper()
    assert not result.sql.upper().startswith("SELECT")


def test_snowflake_metric_translation_uses_common_llm_provider_chain(monkeypatch):
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.converter.common_dax_translator import CommonDAXTranslationResult, CommonDAXTranslator
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.sml.models import (
        AggregationType,
        DataType,
        SMLColumn,
        SMLDataset,
        SMLMetric,
        SMLModel,
    )

    calls = []

    def fake_translate(self, **kwargs):
        calls.append(kwargs)
        return CommonDAXTranslationResult(
            sql='SUM(SALESFACT."UNITS")',
            is_valid=True,
            provider="deepseek",
            attempted_providers=["deepseek"],
        )

    monkeypatch.setattr(CommonDAXTranslator, "translate", fake_translate)

    behavior = ConnectorBehavior(
        databricks={
            "enable_llm_dax_translation": True,
            "llm_dax_provider_order": ["deepseek", "gemini", "groq"],
            "llm_dax_cache_enabled": False,
        }
    )
    model = SMLModel(
        unique_name="Competitive Marketing Analysis",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SalesFact",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="ProductID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Units", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total Complex Units",
                dataset="SalesFact",
                expression="CALCULATE([Total Units], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"Yes\"))",
                aggregation=AggregationType.NONE,
            ),
        ],
    )
    emitter = SnowflakeEmitter(
        SnowflakeConfig(
            account="dummy",
            user="dummy",
            password="dummy",
            warehouse="WH",
            database="DB",
            schema_name="PUBLIC",
        ),
        behavior=behavior,
    )

    ddls = emitter.semantic_view_builder.generate_ddls(model)
    joined = "\n".join(ddls).upper()

    assert calls
    assert calls[0]["metric_name"] == "Total Complex Units"
    assert "SUM(SALESFACT.UNITS::FLOAT)" in joined


def test_databricks_draft_fallback_is_not_cast_null():
    from semabridge.connectors import databricks_publisher

    assert databricks_publisher.DRAFT_MEASURE_SQL == "0"
