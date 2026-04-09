"""Tests for schema reconciliation helpers used by Databricks publishing."""

from semabridge.connectors.schema_reconciler import (
    MissingPrerequisiteException,
    SchemaMapper,
    normalize_column_name,
)


def test_normalize_column_name_applies_expected_rules() -> None:
    assert normalize_column_name("Customer Key") == "customer_key"
    assert normalize_column_name("Labor Costs Variable") == "labor_costs_variable"
    assert normalize_column_name("Revenue %") == "revenue"
    assert normalize_column_name("  Mixed---Case__Name  ") == "mixed_case_name"


def test_schema_mapper_build_mapping_matches_normalized_columns() -> None:
    mapper = SchemaMapper(
        expected_columns=["Customer Key", "Labor Costs Variable", "Missing Column"],
        available_columns=["customer_key", "labor_costs_variable", "other_col"],
    )

    mapped, missing = mapper.build_mapping()

    assert mapped["Customer Key"] == "customer_key"
    assert mapped["Labor Costs Variable"] == "labor_costs_variable"
    assert missing == ["Missing Column"]


def test_generate_view_sql_injects_null_for_missing_when_strategy_null() -> None:
    mapper = SchemaMapper(
        expected_columns=["Customer Key", "Missing Column"],
        available_columns=["customer_key"],
    )

    sql = mapper.generate_view_sql(
        source_table="`main`.`public`.`sales_source`",
        target_view="`main`.`public`.`sales_semantic_view`",
        missing_strategy="null",
    )

    assert "CREATE OR REPLACE VIEW `main`.`public`.`sales_semantic_view` AS" in sql
    assert "`customer_key` AS `Customer Key`" in sql
    assert "NULL AS `Missing Column`" in sql
    assert "FROM `main`.`public`.`sales_source`" in sql


def test_generate_view_sql_raises_structured_exception_when_strategy_raise() -> None:
    mapper = SchemaMapper(
        expected_columns=["Customer Key", "Missing Column"],
        available_columns=["customer_key"],
    )

    try:
        mapper.generate_view_sql(
            source_table="`main`.`public`.`sales_source`",
            target_view="`main`.`public`.`sales_semantic_view`",
            missing_strategy="raise",
        )
        assert False, "Expected MissingPrerequisiteException to be raised"
    except MissingPrerequisiteException as exc:
        payload = exc.to_dict()
        assert payload["error"] == "MISSING_PREREQUISITE"
        assert payload["source_table"] == "`main`.`public`.`sales_source`"
        assert payload["missing_columns"][0]["expected_column"] == "Missing Column"
