import pytest
from unittest.mock import MagicMock
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder


def test_align_partition_by_excluding_replaces_enriched_copy_with_dimension_table():
    """Rule 1: PARTITION BY EXCLUDING must use the declared dimension of the DIMENSION TABLE

    (STORE.STORE, YEAR.YEAR), never an enriched copy on the fact (SALES.STORE_STORE, INDICATORS.YEAR_YEAR).
    """
    sanitizer = MagicMock()
    sanitizer.to_alias = lambda x: x
    sanitizer.sanitize_column = lambda x: x
    builder = MetricsClauseBuilder(
        identifier_sanitizer=sanitizer,
        schema_manager=MagicMock(),
        sanitizer=sanitizer,
        translator=MagicMock(),
        config=MagicMock(),
    )

    builder.emitted_dimensions = {
        "SALES": ["STORE_STORE", "AMOUNT"],
        "STORE": ["STORE", "TYPE"],
        "INDICATORS": ["YEAR_YEAR", "VALUE"],
        "YEAR": ["YEAR"],
    }
    builder.emitted_dim_by_phys = {
        ("SALES", "STORE_STORE"): "STORE_STORE",
        ("STORE", "STORE"): "STORE",
        ("STORE", "TYPE"): "TYPE",
        ("INDICATORS", "YEAR_YEAR"): "YEAR_YEAR",
        ("YEAR", "YEAR"): "YEAR",
    }

    # 1. Enriched copy on fact table SALES."STORE_STORE" -> STORE."STORE"
    sql1 = 'SUM(SALES."AMOUNT") OVER (PARTITION BY EXCLUDING STORE."TYPE", SALES."STORE_STORE")'
    aligned1 = builder._align_partition_by_excluding(sql1)
    assert 'PARTITION BY EXCLUDING STORE."STORE", STORE."TYPE"' in aligned1
    assert "STORE_STORE" not in aligned1

    # 2. Enriched copy on fact table INDICATORS."YEAR_YEAR" -> YEAR."YEAR"
    sql2 = 'SUM(INDICATORS."VALUE") OVER (PARTITION BY EXCLUDING INDICATORS."YEAR_YEAR")'
    aligned2 = builder._align_partition_by_excluding(sql2)
    assert 'PARTITION BY EXCLUDING YEAR."YEAR"' in aligned2
    assert "YEAR_YEAR" not in aligned2


def test_escape_comment_escapes_single_quotes_exactly_once():
    """Rule 2: COMMENT escaping must be exactly once (never produce '''' sequences)."""
    # Plain text with single quote
    assert MetricsClauseBuilder._escape_comment("can't find") == "can''t find"

    # Already escaped text must remain escaped once, not doubled
    assert MetricsClauseBuilder._escape_comment("can''t find") == "can''t find"

    # Text corrupted with multiple quotes collapses to exact single escape
    assert MetricsClauseBuilder._escape_comment("can''''t find") == "can''t find"


def test_loo_decomposes_all_fact_table_to_window_total():
    """Leave-one-out calculation decomposing ALL(table) to window total without introducing raw MAX(...) aggregate."""
    from semabridge.converter.dax_translator import DAXTranslator

    translator = DAXTranslator()
    dax = """VAR FactorAvg = AVERAGE('Cases'[CSAT])
VAR AllAvg = CALCULATE(AVERAGE('Cases'[CSAT]), ALL('Cases'))
VAR AllAvgExcept =
    CALCULATE(
        AVERAGE('Cases'[CSAT]),
        FILTER(ALL('Cases'), 'Cases'[Topic] <> SELECTEDVALUE('Cases'[Topic]))
    )
RETURN
    1 - (AllAvgExcept / AllAvg)"""

    res = translator.translate(dax, table_alias="CASES", dataset_name="Cases")
    assert res.is_success is True
    # Must NOT have MAX(CASES."_ALL_AVG_CSAT")
    assert "MAX(" not in res.sql
    # Must have decomposed window total
    assert 'OVER (PARTITION BY EXCLUDING CASES."TOPIC")' in res.sql


def test_partition_by_excluding_declared_dimension_passes_predeploy():
    """Predeploy check accepts declared dimensions and table-prefixed columns in PARTITION BY EXCLUDING."""
    sanitizer = MagicMock()
    sanitizer.to_alias = lambda x: x
    sanitizer.sanitize_column = lambda x: x
    builder = MetricsClauseBuilder(
        identifier_sanitizer=sanitizer,
        schema_manager=MagicMock(),
        sanitizer=sanitizer,
        translator=MagicMock(),
        config=MagicMock(),
    )
    builder._dataset_col_lookup = {
        "CASES": {"CSAT", "TOPIC", "SUBJECT", "PRODUCTSEQ", "AGENT", "STATUS"}
    }
    builder._dataset_aliases = {"Cases": "CASES"}
    builder.emitted_dimensions = {
        "CASES": ["TOPIC", "SUBJECT", "CASES_PRODUCTSEQ", "AGENT"]
    }
    builder.emitted_dim_by_phys = {
        ("CASES", "PRODUCTSEQ"): "CASES_PRODUCTSEQ"
    }

    # Window metric over declared dimension CASES_PRODUCTSEQ
    m_expr = (
        'CASE WHEN (1) IS NULL AND ((((SUM(CASES."CSAT_SUM"::FLOAT) OVER (PARTITION BY EXCLUDING CASES."CASES_PRODUCTSEQ") - CASES."CSAT_SUM") / '
        'NULLIF(SUM(CASES."CSAT_CNT"::FLOAT) OVER (PARTITION BY EXCLUDING CASES."CASES_PRODUCTSEQ") - CASES."CSAT_CNT", 0))) / '
        'NULLIF((SUM(CASES."CSAT_SUM"::FLOAT) OVER (PARTITION BY EXCLUDING CASES."CASES_PRODUCTSEQ") / '
        'NULLIF(SUM(CASES."CSAT_CNT"::FLOAT) OVER (PARTITION BY EXCLUDING CASES."CASES_PRODUCTSEQ"), 0)), 0)) IS NULL THEN NULL ELSE 1 END'
    )

    reason = builder._run_predeploy_checks(
        m_name="CSAT_IMPACT_PRODUCTS",
        alias="CASES",
        m_expr=m_expr,
        dax_expr="",
        dataset_col_lookup=builder._dataset_col_lookup,
        dataset_aliases=builder._dataset_aliases,
        relationships=[],
        defined_metric_names={"CSAT_SUM", "CSAT_CNT"}
    )
    assert reason is None


