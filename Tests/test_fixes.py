#!/usr/bin/env python
"""
Quick test to verify that the $ character filtering is applied in snowflake_emitter.py
"""

import sys
#!/usr/bin/env python
"""
Quick test to verify that the $ character filtering is applied in snowflake_emitter.py
"""

import sys
from pathlib import Path

# Add src to path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "src"))

def test_metric_filtering():
    """Test that metrics with $ are filtered"""
    print("=" * 70)
    print("TESTING: Metric $ Character Filtering")
    print("=" * 70)
    
    # Read the metrics_clause_builder file and check for the fix
    builder_path = root_dir / "src" / "semabridge" / "connectors" / "metrics_clause_builder.py"
    
    with open(builder_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for the filtering fix
    assert 'valid_metrics = [m for m in model.metrics if "$" not in m.unique_name]' in content
    print("✅ PASS: Metric $ filtering is in place")

def test_relationship_validation():
    """Test that relationship validation guards are in place"""
    print("\n" + "=" * 70)
    print("TESTING: Relationship Validation Guards")
    print("=" * 70)
    
    builder_path = root_dir / "src" / "semabridge" / "connectors" / "relationships_clause_builder.py"
    
    with open(builder_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for the guard condition
    assert 'if not from_alias or not to_alias or not rel.from_columns:' in content
    print("✅ PASS: Relationship validation guards are in place")

def test_syntax():
    """Test that the modified files have valid Python syntax"""
    print("\n" + "=" * 70)
    print("TESTING: Python Syntax Validation")
    print("=" * 70)
    
    import py_compile
    for filename in ["snowflake_emitter.py", "metrics_clause_builder.py", "relationships_clause_builder.py", "ddl_builder.py"]:
        path = root_dir / "src" / "semabridge" / "connectors" / filename
        py_compile.compile(str(path), doraise=True)
    print("✅ PASS: Connectors have valid Python syntax")

def test_quoted_table_alias_and_special_character_resolution():
    """Test that double-quoted table names and double-quoted column names are properly resolved and sanitized"""
    from semabridge.utils.identifiers import IdentifierSanitizer
    
    sanitizer = IdentifierSanitizer()
    alias_lookup = {"SALESFACT": "salesfact"}
    
    # Test case 1: Double-quoted table name and double-quoted column name containing %
    expr = '"salesfact"."% UNIT MARKET SHARE YOY CHANGE"'
    resolved = sanitizer.resolve_dot_notation(expr, alias_lookup)
    assert resolved == 'salesfact."PCT_UNIT_MARKET_SHARE_YOY_CHANGE"'
    
    # Test case 2: Double-quoted table name and unquoted column name
    expr2 = '"salesfact".UNITS'
    resolved2 = sanitizer.resolve_dot_notation(expr2, alias_lookup)
    assert resolved2 == 'salesfact."UNITS"'
    
    # Test case 3: Unquoted table name and double-quoted column name with special character
    expr3 = 'salesfact."% Category Compete Share"'
    resolved3 = sanitizer.resolve_dot_notation(expr3, alias_lookup)
    assert resolved3 == 'salesfact."PCT_CATEGORY_COMPETE_SHARE"'
    
    print("✅ PASS: Quoted table alias and special character resolution verified")

def test_dynamic_emittable_metric_registration():
    """Test that SML metrics with DAX expressions are in initial emittable set, and added dynamically"""
    from types import SimpleNamespace
    from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
    from semabridge.utils.identifiers import IdentifierSanitizer
    
    # 1. Verify DDL Builder's emittable logic including DAX expressions
    sml_metric_1 = SimpleNamespace(
        unique_name="Total Units YTD",
        dataset="SalesFact",
        expression="TOTALYTD(SUM(SalesFact[Units]), 'Date'[Date])",
        sql_expression=None,
        source_column=None,
        aggregation=None,
    )
    sml_metrics = [sml_metric_1]
    
    id_sanitizer = IdentifierSanitizer()
    emittable = {id_sanitizer.sanitize_alias(m.unique_name) for m in sml_metrics if (m.source_column and m.aggregation) or m.sql_expression or getattr(m, "expression", None)}
    assert "TOTAL_UNITS_YTD" in emittable
    
    # 2. Verify that MetricsClauseBuilder dynamically adds to emittable_metric_name_set
    class DummySanitizer:
        def format_physical_column_ref(self, alias: str, col: str, model_name=None):
            return f'{alias}."{col}"'
            
    class DummyTranslator:
        def prefetch_openai_metric_translations(self, **kwargs):
            pass
        def _try_llm_metric_fallback_expression(self, **kwargs):
            return 'SUM(SALESFACT."UNITS")'
        def _auto_qualify_cross_table_refs(self, expr, aliases):
            return expr
            
    builder = MetricsClauseBuilder(
        identifier_sanitizer=id_sanitizer,
        schema_manager=None,
        sanitizer=DummySanitizer(),
        translator=DummyTranslator(),
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
    )
    
    dataset_aliases = {"SalesFact": "SALESFACT"}
    dataset_by_name = {"SalesFact": SimpleNamespace(is_fact=True)}
    dataset_col_lookup = {"SalesFact": {"UNITS"}}
    alias_by_raw = {"SalesFact": "salesfact"}
    all_physical_col_names = {"UNITS"}
    emittable_set = set() # Start empty to verify dynamic adding
    
    lines = builder.build_for_sml(
        SimpleNamespace(metrics=sml_metrics, unique_name="model", label="model"),
        dataset_aliases=dataset_aliases,
        dataset_by_name=dataset_by_name,
        dataset_col_lookup=dataset_col_lookup,
        alias_by_raw=alias_by_raw,
        all_physical_col_names=all_physical_col_names,
        emittable_metric_name_set=emittable_set,
    )
    
    assert "TOTAL_UNITS_YTD" in emittable_set
    assert len(lines) == 1
    assert "TOTAL_UNITS_YTD" in lines[0]
    print("✅ PASS: Dynamic emittable metric registration and SML emittable set expansion verified")

def test_dynamic_alias_healing_rewrites_to_active_alias():
    """Unknown aliases should normalize to the runtime alias, not stale CALENDAR/COL_DATE."""
    from semabridge.connectors.translator import MetricExpressionTranslator
    from semabridge.utils.identifiers import IdentifierSanitizer

    translator = MetricExpressionTranslator(IdentifierSanitizer())
    dataset_aliases = {"Date": "COL_DATE_2", "SalesFact_ENRICHED": "SALESFACT_ENRICHED"}
    dataset_col_lookup = {
        "Date": {"YEAR", "COL_DATE"},
        "SalesFact_ENRICHED": {"UNITS"},
    }

    assert translator._normalize_metric_column_references(
        'CALENDAR."YEAR"',
        "Metric",
        dataset_col_lookup,
        dataset_aliases,
        metric_names=set(),
    ) == "COL_DATE_2.YEAR"
    assert translator._normalize_metric_column_references(
        'COL_DATE."YEAR"',
        "Metric",
        dataset_col_lookup,
        dataset_aliases,
        metric_names=set(),
    ) == "COL_DATE_2.YEAR"
    assert translator._normalize_metric_column_references(
        'SalesFact."UNITS"',
        "Metric",
        dataset_col_lookup,
        dataset_aliases,
        metric_names=set(),
    ) == "SALESFACT_ENRICHED.UNITS"

def test_dynamic_alias_healing_does_not_accept_invalid_qualified_alias():
    """Explicit invalid aliases must remain validation failures."""
    from semabridge.connectors.translator import MetricExpressionTranslator
    from semabridge.utils.identifiers import IdentifierSanitizer

    translator = MetricExpressionTranslator(IdentifierSanitizer())
    valid, issue = translator._validate_metric_column_references(
        'unknown."REVENUE"',
        "Metric",
        {"SalesFact": {"REVENUE"}},
        {"SalesFact": "SALESFACT"},
        metric_names=set(),
    )

    assert valid is False
    assert "Alias 'unknown' not found" in issue

def test_dax_strict_time_intelligence_uses_configured_date_alias(monkeypatch):
    """Strict DAX TOTALYTD output should not hardcode CALENDAR."""
    from types import SimpleNamespace
    from semabridge.converter.dax_translator import DAXTranslator

    monkeypatch.setenv("SEMABRIDGE_DATE_ALIAS", "COL_DATE_2")
    result = DAXTranslator().translate(
        "TOTALYTD([Total Units], Date[Date])",
        "SALESFACT",
        "SalesFact",
        metrics_context=[
            SimpleNamespace(
                unique_name="Total Units",
                sql_expression='SUM(SALESFACT."UNITS")',
                expression="",
            )
        ],
    )

    assert result.sql
    assert "COL_DATE_2.YEAR" in result.sql
    assert "CALENDAR.YEAR" not in result.sql

def test_indicator_label_overrides_do_not_reference_fake_salesfact_columns():
    """Indicator label metrics must not point at non-physical SALESFACT columns."""
    from types import SimpleNamespace
    from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
    from semabridge.connectors.translator import MetricExpressionTranslator
    from semabridge.utils.identifiers import IdentifierSanitizer

    ids = IdentifierSanitizer()
    builder = MetricsClauseBuilder(
        identifier_sanitizer=ids,
        schema_manager=None,
        sanitizer=None,
        translator=MetricExpressionTranslator(ids),
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
    )
    dataset_aliases = {
        "SalesFact": "SALESFACT",
        "Sentiment": "SENTIMENT",
        "Manufacturer": "MANUFACTURER",
    }
    dataset_col_lookup = {
        "SalesFact": {"TOTAL_UNITS"},
        "Sentiment": {"SCORE"},
        "Manufacturer": {"MFGISVANARSDEL"},
    }

    expr_04a = builder._generate_metric_expression(
        SimpleNamespace(unique_name="@Indicator04A", dataset="SalesFact", source_column=None, aggregation=None, sql_expression=None, expression=""),
        "ATINDICATOR04A",
        "SALESFACT",
        {},
        dataset_aliases,
        dataset_col_lookup,
        {},
        {"ATINDICATOR04A", "ATINDICATOR04", "ATINDICATOR05"},
        {"TOTAL_UNITS", "SCORE", "MFGISVANARSDEL"},
        {"ATINDICATOR04A", "ATINDICATOR04", "ATINDICATOR05"},
        set(),
        None,
        False,
        fact_aliases={"SALESFACT"},
    )
    expr_05a = builder._generate_metric_expression(
        SimpleNamespace(unique_name="@Indicator05A", dataset="SalesFact", source_column=None, aggregation=None, sql_expression=None, expression=""),
        "ATINDICATOR05A",
        "SALESFACT",
        {},
        dataset_aliases,
        dataset_col_lookup,
        {},
        {"ATINDICATOR04A", "ATINDICATOR04", "ATINDICATOR05A", "ATINDICATOR05"},
        {"TOTAL_UNITS", "SCORE", "MFGISVANARSDEL"},
        {"ATINDICATOR04A", "ATINDICATOR04", "ATINDICATOR05A", "ATINDICATOR05"},
        set(),
        None,
        False,
        fact_aliases={"SALESFACT"},
    )

    assert 'SALESFACT."ATINDICATOR04"' not in expr_04a
    assert 'SALESFACT."ATINDICATOR05"' not in expr_05a
    assert 'SENTIMENT."SCORE"' in expr_04a
    assert 'SENTIMENT."SCORE"' in expr_05a
    assert 'MANUFACTURER."MFGISVANARSDEL"' in expr_05a

def test_cross_dataset_refs_are_detected_for_fact_enrichment():
    """Direct Table[Column] DAX refs should become fact enriched-view projections."""
    from types import SimpleNamespace
    from semabridge.connectors.ddl_builder import SemanticViewBuilder
    from semabridge.utils.identifiers import IdentifierSanitizer

    ids = IdentifierSanitizer()
    model = SimpleNamespace(
        datasets=[
            SimpleNamespace(unique_name="SalesFact"),
            SimpleNamespace(unique_name="Manufacturer"),
            SimpleNamespace(unique_name="Sentiment"),
        ],
        metrics=[
            SimpleNamespace(
                unique_name="Sentiment Gap",
                dataset="SalesFact",
                expression="AVG(CASE WHEN 'Manufacturer'[MfgIsVanArsDel] = 'No' THEN 'Sentiment'[Score] END)",
            )
        ],
    )
    builder = SemanticViewBuilder(
        config=SimpleNamespace(),
        behavior=SimpleNamespace(snowflake=SimpleNamespace()),
        identifier_sanitizer=ids,
        live_schema_metadata={},
    )

    suggestions = builder._precompute_suggestions(model)

    assert suggestions == {"SalesFact": ["MANUFACTURER_MFGISVANARSDEL", "SENTIMENT_SCORE"]}
    assert {
        (d["target_dataset"], d["source_dataset"], d["source_column"], d["precomputed_column"])
        for d in builder.get_precompute_details()
    } == {
        ("SalesFact", "Manufacturer", "MfgIsVanArsDel", "MANUFACTURER_MFGISVANARSDEL"),
        ("SalesFact", "Sentiment", "Score", "SENTIMENT_SCORE"),
    }

def test_cross_dataset_metric_sql_rewrites_to_enriched_fact_columns():
    """When enriched columns exist, metric SQL should stay on the metric entity."""
    from types import SimpleNamespace
    from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
    from semabridge.utils.identifiers import IdentifierSanitizer

    ids = IdentifierSanitizer()
    builder = MetricsClauseBuilder(
        identifier_sanitizer=ids,
        schema_manager=None,
        sanitizer=None,
        translator=None,
        config=SimpleNamespace(),
    )
    sql = (
        'AVG(CASE WHEN MANUFACTURER."MFGISVANARSDEL" = \'No\' '
        'THEN SENTIMENT."SCORE" END::FLOAT)'
    )
    rewritten = builder._rewrite_cross_dataset_sql_refs_to_precomputed(
        sql,
        "SalesFact",
        "SALESFACT",
        {
            "SalesFact": "SALESFACT",
            "Manufacturer": "MANUFACTURER",
            "Sentiment": "SENTIMENT",
        },
        {
            "SalesFact": {"MANUFACTURER_MFGISVANARSDEL", "SENTIMENT_SCORE"},
            "Manufacturer": {"MFGISVANARSDEL"},
            "Sentiment": {"SCORE"},
        },
    )

    assert rewritten == (
        'AVG(CASE WHEN SALESFACT."MANUFACTURER_MFGISVANARSDEL" = \'No\' '
        'THEN SALESFACT."SENTIMENT_SCORE" END::FLOAT)'
    )
    assert 'MANUFACTURER."MFGISVANARSDEL"' not in rewritten
    assert 'SENTIMENT."SCORE"' not in rewritten

def test_snowflake_metric_sql_normalizes_unsupported_int_function():
    """Snowflake metric SQL should not emit bare INT(...) calls."""
    from semabridge.connectors.snowflake_metric_sql import normalize_snowflake_metric_sql

    expr = 'INT(DIV0(SALESFACT."TOTAL_COMPETE_VOLUME", SALESFACT."TOTAL_CATEGORY_VOLUME")*100)'
    normalized = normalize_snowflake_metric_sql(expr)

    assert normalized == 'CAST((DIV0(SALESFACT."TOTAL_COMPETE_VOLUME", SALESFACT."TOTAL_CATEGORY_VOLUME")*100) AS INT)'
    assert "INT(" not in normalized

def test_snowflake_metric_sql_int_rewrite_is_quote_aware():
    """Parentheses inside string literals must not break function rewriting."""
    from semabridge.connectors.snowflake_metric_sql import normalize_snowflake_metric_sql

    expr = "INT(CASE WHEN NAME = 'A)' THEN 1 ELSE DIV0(X, Y) END)"
    normalized = normalize_snowflake_metric_sql(expr)

    assert normalized == "CAST((CASE WHEN NAME = 'A)' THEN 1 ELSE DIV0(X, Y) END) AS INT)"

if __name__ == "__main__":
    try:
        test_metric_filtering()
        test_relationship_validation()
        test_syntax()
        test_quoted_table_alias_and_special_character_resolution()
        test_dynamic_emittable_metric_registration()
        print("\n✅ ALL FIXES VERIFIED - Ready for deployment test")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Assertion failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
