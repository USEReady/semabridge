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

