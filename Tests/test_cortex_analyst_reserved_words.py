"""Tests for Cortex Analyst reserved-word auto-sanitization.

Issue: Cortex Analyst rejects identifiers that are reserved YAML keywords
       (e.g. 'TO', 'FROM', 'DATE').  SemaBridge must rename them automatically
       with a COL_ prefix so that the generated DDL is always valid.

Covers:
  - CORTEX_ANALYST_RESERVED_WORDS set contains the critical offenders
  - IdentifierSanitizer.sanitize_column() prefixes reserved names with COL_
  - IdentifierSanitizer.is_cortex_analyst_reserved() helper works correctly
  - Non-reserved normal column names are NOT affected
  - SemanticViewBuilder._validate_model_health() emits expected warnings
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from semabridge.utils.identifiers import (
    CORTEX_ANALYST_RESERVED_WORDS,
    SNOWFLAKE_RESERVED_WORDS,
    IdentifierSanitizer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sanitizer(**kwargs) -> IdentifierSanitizer:
    return IdentifierSanitizer(suppress_reserved=True, **kwargs)


# ---------------------------------------------------------------------------
# CORTEX_ANALYST_RESERVED_WORDS membership tests
# ---------------------------------------------------------------------------

class TestCortexAnalystReservedWordSet:
    """The set must contain the specific words that caused real test failures."""

    @pytest.mark.parametrize("word", [
        "to", "from", "date", "group", "order", "select", "where",
        "join", "table", "column", "view", "count", "sum", "total",
        "and", "or", "null", "true", "false", "case", "when",
    ])
    def test_critical_words_are_present(self, word: str):
        assert word in CORTEX_ANALYST_RESERVED_WORDS, (
            f"'{word}' must be in CORTEX_ANALYST_RESERVED_WORDS "
            f"(causes Cortex Analyst YAML validation error)"
        )

    def test_cortex_words_are_subset_of_snowflake_reserved(self):
        """All Cortex Analyst reserved words must also be in the combined set."""
        assert CORTEX_ANALYST_RESERVED_WORDS.issubset(SNOWFLAKE_RESERVED_WORDS), (
            "CORTEX_ANALYST_RESERVED_WORDS must be fully merged into "
            "SNOWFLAKE_RESERVED_WORDS via the | operator"
        )

    def test_normal_column_names_not_reserved(self):
        normal = {"customer_id", "revenue", "product_name", "hire_date_col", "start_date"}
        for name in normal:
            assert name not in CORTEX_ANALYST_RESERVED_WORDS, (
                f"'{name}' should NOT be a reserved word"
            )


# ---------------------------------------------------------------------------
# IdentifierSanitizer.is_cortex_analyst_reserved() tests
# ---------------------------------------------------------------------------

class TestIsCortexAnalystReserved:
    @pytest.mark.parametrize("name", ["to", "TO", "To", " to "])
    def test_case_insensitive(self, name: str):
        assert IdentifierSanitizer.is_cortex_analyst_reserved(name)

    @pytest.mark.parametrize("name", ["customer_id", "revenue_total", "to_date", "from_date"])
    def test_normal_names_not_reserved(self, name: str):
        assert not IdentifierSanitizer.is_cortex_analyst_reserved(name)


# ---------------------------------------------------------------------------
# IdentifierSanitizer.sanitize_column() — reserved word renaming
# ---------------------------------------------------------------------------

class TestSanitizeColumnReservedWords:
    """Ensure reserved words are prefixed with COL_ by sanitize_column()."""

    @pytest.fixture
    def s(self) -> IdentifierSanitizer:
        return make_sanitizer()

    @pytest.mark.parametrize("col, expected", [
        ("TO",           "COL_TO"),
        ("to",           "COL_TO"),
        ("FROM",         "COL_FROM"),
        ("DATE",         "COL_DATE"),
        ("GROUP",        "COL_GROUP"),
        ("ORDER",        "COL_ORDER"),
        ("COUNT",        "COL_COUNT"),
        ("SUM",          "COL_SUM"),
        ("TABLE",        "COL_TABLE"),
        ("VIEW",         "COL_VIEW"),
        ("NULL",         "COL_NULL"),
        ("TRUE",         "COL_TRUE"),
        ("FALSE",        "COL_FALSE"),
        ("TOTAL",        "COL_TOTAL"),
    ])
    def test_reserved_word_gets_col_prefix(self, s: IdentifierSanitizer, col: str, expected: str):
        result = s.sanitize_column(col)
        assert result == expected, (
            f"sanitize_column('{col}') should return '{expected}' but got '{result}'"
        )

    @pytest.mark.parametrize("col", [
        "CUSTOMER_ID", "REVENUE", "PRODUCT_NAME",
        "HIRE_DATE_COL", "START_DATE", "END_DATE",
        "TO_DATE", "FROM_DATE", "ORDER_NUMBER",
    ])
    def test_normal_columns_unchanged(self, s: IdentifierSanitizer, col: str):
        result = s.sanitize_column(col)
        # These should not get COL_ prefix
        assert not result.startswith("COL_"), (
            f"sanitize_column('{col}') incorrectly returned '{result}' with COL_ prefix"
        )

    def test_suppress_reserved_false_does_not_prefix(self):
        """When suppress_reserved=False the COL_ prefix must NOT be applied."""
        s = IdentifierSanitizer(suppress_reserved=False)
        assert s.sanitize_column("TO") == "TO"
        assert s.sanitize_column("FROM") == "FROM"


# ---------------------------------------------------------------------------
# SemanticViewBuilder._validate_model_health() warnings
# ---------------------------------------------------------------------------

class TestValidateModelHealth:
    """Verify the model health validator emits the expected structured warnings."""

    def _make_builder(self):
        from semabridge.connectors.ddl_builder import SemanticViewBuilder

        config = MagicMock()
        config.database = "DB"
        config.schema_name = "SCHEMA"
        config.naming_strategy = "deterministic_hash"

        behavior = MagicMock()
        behavior.compatibility.suppress_reserved_words = True
        behavior.compatibility.force_uppercase = True
        behavior.semantic_model.sync_all_attributes = True
        behavior.snowflake.fail_on_missing_relationships = False

        sanitizer = IdentifierSanitizer()

        builder = SemanticViewBuilder(
            config=config,
            behavior=behavior,
            identifier_sanitizer=sanitizer,
            live_schema_metadata={},
        )
        return builder

    def _make_column(self, name: str):
        col = SimpleNamespace()
        col.unique_name = name
        col.is_key = False
        col.synonyms = []
        return col

    def _make_dataset(self, name: str, columns=None):
        ds = SimpleNamespace()
        ds.unique_name = name
        ds.columns = columns or []
        return ds

    def _make_metric(self, dataset: str):
        m = SimpleNamespace()
        m.dataset = dataset
        m.unique_name = f"metric_{dataset}"
        return m

    def _make_relationship(self, from_ds: str, to_ds: str):
        rel = SimpleNamespace()
        rel.is_active = True
        rel.from_dataset = from_ds
        rel.to_dataset = to_ds
        return rel

    # --- reserved word audit ---

    def test_reserved_word_warning_emitted(self, caplog):
        builder = self._make_builder()
        model = SimpleNamespace(
            unique_name="EmployeeHiring",
            label="EmployeeHiring",
            datasets=[
                self._make_dataset("employee", [self._make_column("TO")]),
            ],
            metrics=[],
            relationships=[],
        )

        with caplog.at_level(logging.WARNING):
            builder._validate_model_health(model, model_kind="SML")

        assert any("[RESERVED WORD]" in r.message for r in caplog.records), (
            "Expected a [RESERVED WORD] warning for column 'TO'"
        )
        assert any("TO" in r.message for r in caplog.records)

    def test_no_reserved_word_warning_for_normal_columns(self, caplog):
        builder = self._make_builder()
        model = SimpleNamespace(
            unique_name="CleanModel",
            label="CleanModel",
            datasets=[
                self._make_dataset("employee", [
                    self._make_column("HIRE_DATE"),
                    self._make_column("EMPLOYEE_ID"),
                ]),
            ],
            metrics=[],
            relationships=[],
        )

        with caplog.at_level(logging.WARNING):
            builder._validate_model_health(model, model_kind="SML")

        reserved_warnings = [r for r in caplog.records if "[RESERVED WORD]" in r.message]
        assert not reserved_warnings

    # --- orphan table detection ---

    def test_orphan_table_warning_when_no_metrics_no_rels(self, caplog):
        builder = self._make_builder()
        model = SimpleNamespace(
            unique_name="CoreFinanceV1",
            label="CoreFinanceV1",
            datasets=[
                self._make_dataset("FACT"),
                self._make_dataset("ACCOUNTS"),   # ← no metrics, no relationship
            ],
            metrics=[self._make_metric("FACT")],
            relationships=[],
        )

        with caplog.at_level(logging.WARNING):
            builder._validate_model_health(model, model_kind="SML")

        assert any("[ORPHAN TABLE]" in r.message for r in caplog.records), (
            "Expected [ORPHAN TABLE] warning for ACCOUNTS"
        )
        assert any("ACCOUNTS" in r.message for r in caplog.records)

    def test_no_orphan_warning_when_relationship_exists(self, caplog):
        builder = self._make_builder()
        model = SimpleNamespace(
            unique_name="FixedModel",
            label="FixedModel",
            datasets=[
                self._make_dataset("FACT"),
                self._make_dataset("ACCOUNTS"),
            ],
            metrics=[self._make_metric("FACT")],
            relationships=[
                self._make_relationship("FACT", "ACCOUNTS"),
            ],
        )

        with caplog.at_level(logging.WARNING):
            builder._validate_model_health(model, model_kind="SML")

        orphan_warnings = [r for r in caplog.records if "[ORPHAN TABLE]" in r.message]
        assert not orphan_warnings

    # --- large model warning ---

    def test_large_model_warning_above_threshold(self, caplog):
        builder = self._make_builder()
        # Create 25 datasets (above threshold of 20)
        datasets = [self._make_dataset(f"TABLE_{i}") for i in range(25)]
        model = SimpleNamespace(
            unique_name="BigModel",
            label="BigModel",
            datasets=datasets,
            metrics=[],
            relationships=[],
        )

        with caplog.at_level(logging.WARNING):
            builder._validate_model_health(model, model_kind="SML")

        assert any("[MODEL HEALTH]" in r.message and "25 tables" in r.message
                   for r in caplog.records), (
            "Expected large model warning for 25-table model"
        )

    def test_no_large_model_warning_below_threshold(self, caplog):
        builder = self._make_builder()
        datasets = [self._make_dataset(f"TABLE_{i}") for i in range(10)]
        model = SimpleNamespace(
            unique_name="SmallModel",
            label="SmallModel",
            datasets=datasets,
            metrics=[],
            relationships=[],
        )

        with caplog.at_level(logging.WARNING):
            builder._validate_model_health(model, model_kind="SML")

        large_warnings = [r for r in caplog.records
                          if "[MODEL HEALTH]" in r.message and "tables (threshold" in r.message]
        assert not large_warnings
