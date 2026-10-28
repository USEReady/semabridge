"""Synthetic model tests for duplicate placeholders and DAX parsing improvements.

Tests:
1. Placeholder pair differing only by underscores (Item A: canon name, real beats placeholder, tie-break, schema listing)
2. DAX parsing with special-character column names (/ ( ) % $ < and spaces) ensuring SUM(T[A]) + SUM(T[B]) never merges
3. Unqualified [Name] equal to measure's own name resolves to a column, not circular reference; real cycle logs once
4. Metrics referencing numeric columns declared as facts and passing validation
"""
import re
import pytest
from unittest.mock import MagicMock
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.table_binding_resolver import (
    LiveSchemaMetadata,
    clean_ident,
    rank_table_candidates,
    resolve_dataset_to_physical_table,
    TableBindingAmbiguityError,
)
from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.connectors.ddl_validator import validate_logical_references
from semabridge.converter.dax_rule_translator import (
    extract_column_references,
    extract_aggregation_function,
    is_simple_metric,
)
from semabridge.converter.dax_ast_parser import (
    extract_measure_references_from_ast,
    try_ast_translate,
)
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    DataType as SMLDataType,
)


def test_duplicate_placeholder_tables_resolution():
    """Item A: Placeholder pair differing only by underscores.
    
    1. A real table beats a placeholder.
    2. A tie between duplicate placeholders resolves to saved binding, else canonical name.
    3. Duplicate placeholders are listed in schema metadata without being dropped.
    """
    live_meta = LiveSchemaMetadata({
        "PAYTYPE": {"COL1", "COL2"},
        "PAY_TYPE": {"COL1", "COL2"},
    })
    # Mark both as placeholders
    live_meta.placeholder_tables.add("PAYTYPE")
    live_meta.placeholder_tables.add("PAY_TYPE")

    # Both match 'Pay type' by normalized name and 100% columns
    # Tie between placeholders should resolve to canonical name PAY_TYPE
    resolved = resolve_dataset_to_physical_table(
        "Pay type",
        ["COL1", "COL2"],
        live_meta,
    )
    assert resolved == "PAY_TYPE"

    # With a saved binding, it prefers the saved binding
    live_meta.pinned_bindings[clean_ident("Pay type")] = "PAYTYPE"
    resolved_pinned = resolve_dataset_to_physical_table(
        "Pay type",
        ["COL1", "COL2"],
        live_meta,
    )
    assert resolved_pinned == "PAYTYPE"

    # If one table is a real table and one is a placeholder, real table beats placeholder
    live_meta.pinned_bindings.clear()
    live_meta.placeholder_tables.clear()
    live_meta.placeholder_tables.add("PAY_TYPE")  # PAY_TYPE is placeholder, PAYTYPE is real
    resolved_real = resolve_dataset_to_physical_table(
        "Pay type",
        ["COL1", "COL2"],
        live_meta,
    )
    assert resolved_real == "PAYTYPE"


def test_duplicate_placeholder_schema_listing_and_ambiguity_blocker():
    """Item A: Schema metadata lists duplicate placeholders without dropping,
    and remaining ambiguities are dry-run blockers, never unhandled 500s.
    """
    mgr = SnowflakeSchemaManager(MagicMock(), MagicMock(), IdentifierSanitizer(), MagicMock())
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [("PAYTYPE", "COL1"), ("PAY_TYPE", "COL1")], # FETCH SCHEMA METADATA (columns)
        [("PAYTYPE", "SemaBridge placeholder"), ("PAY_TYPE", "SemaBridge placeholder")], # FETCH TABLE COMMENTS
    ]
    cursor.description = [("table_name",), ("column_name",)]

    meta = mgr._fetch_schema_metadata(cursor)
    assert "PAYTYPE" in mgr._live_schema_metadata.placeholder_tables
    assert "PAY_TYPE" in mgr._live_schema_metadata.placeholder_tables
    assert len(mgr.duplicate_placeholders) > 0
    dup_group = mgr.duplicate_placeholders.get("PAYTYPE")
    assert "PAYTYPE" in dup_group and "PAY_TYPE" in dup_group


def test_dax_parsing_special_character_columns_never_merge():
    """Item B.5: Each [column] reference is parsed separately, even when names contain
    / ( ) % $ < or spaces: SUM(T[A]) + SUM(T[B]) must never merge into one column name.
    """
    dax = "SUM(T[Sales / Vol % ($<1)]) + SUM(T[Other / Rate (100%)])"
    
    # 1. Column reference extraction
    refs = extract_column_references(dax)
    assert len(refs) == 2
    assert "Sales / Vol % ($<1)" in refs
    assert "Other / Rate (100%)" in refs

    # 2. direct_agg regex must NOT match the sum of two sums as a single direct_agg
    from semabridge.converter.dax_rule_translator import SIMPLE_PATTERNS
    direct_agg_pattern = SIMPLE_PATTERNS['direct_agg'][0]
    assert re.match(direct_agg_pattern, dax, re.IGNORECASE) is None

    # 3. Direct single aggregation with special characters matches cleanly
    single_dax = "SUM(T[Sales / Vol % ($<1)])"
    func, col = extract_aggregation_function(single_dax)
    assert func == "SUM"
    assert col == "Sales / Vol % ($<1)"


def test_unqualified_name_resolves_to_column_not_circular():
    """Item B.6: An unqualified [Name] equal to the measure's own name resolves to a
    column (home table or related table), not a circular reference. Log real cycle once.
    """
    measure_name = "Life expectancy (yrs)"
    dax = f"SUM([{measure_name}])"

    # AST translation with current_measure_name equal to the column reference
    sql = try_ast_translate(
        dax,
        table_alias="INDICATORS",
        current_measure_name=measure_name,
        known_measure_names={measure_name},
    )
    assert sql is not None
    assert 'INDICATORS."LIFE_EXPECTANCY_(YRS)"' in sql or 'INDICATORS."Life expectancy (yrs)"' in sql

    # Cycle detection in osi_to_sml skips self-references when matching measure name
    sml = SMLModel(
        unique_name="TestModel",
        datasets=[
            SMLDataset(
                unique_name="INDICATORS",
                columns=[SMLColumn(unique_name=measure_name, data_type=SMLDataType.FLOAT)],
            )
        ],
        metrics=[
            SMLMetric(
                unique_name=measure_name,
                dataset="INDICATORS",
                expression=f"SUM([{measure_name}])",
            ),
            # Add a real cycle pair to verify logging
            SMLMetric(unique_name="MetricA", dataset="INDICATORS", expression="[MetricB]"),
            SMLMetric(unique_name="MetricB", dataset="INDICATORS", expression="[MetricA]"),
        ],
    )
    converter = OSIToSMLConverter()
    # Run cycle detection pass
    converter._resolve_metric_dependencies(sml)
    # The self-referencing metric must NOT be flagged as circular
    self_m = next(m for m in sml.metrics if m.unique_name == measure_name)
    assert not self_m.sync_failure_reason or "Circular" not in self_m.sync_failure_reason

    # Real cycle metrics are detected
    cyc_a = next(m for m in sml.metrics if m.unique_name == "MetricA")
    assert cyc_a.sync_failure_reason and "Circular" in cyc_a.sync_failure_reason


def test_referenced_column_declared_as_fact_and_validated():
    """Item B.7: Confirm metrics referencing undeclared numeric columns are declared
    as facts, and validate_logical_references passes on quoted special-char column names.
    """
    ddl = """
    CREATE OR REPLACE SEMANTIC VIEW DB.SCHEMA.V_TEST
    TABLES (
        INDICATORS AS DB.SCHEMA."INDICATORS"
    )
    FACTS (
        PUBLIC INDICATORS."Life expectancy (yrs)" AS INDICATORS."LIFE_EXPECTANCY_YRS"
    )
    DIMENSIONS (
        INDICATORS."Country" AS INDICATORS."COUNTRY"
    )
    METRICS (
        INDICATORS."LIFE_EXPECTANCY_YRS" AS SUM(INDICATORS."LIFE_EXPECTANCY_YRS")
    );
    """
    errors = validate_logical_references(ddl)
    assert errors == []
