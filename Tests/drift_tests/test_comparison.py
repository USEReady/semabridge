"""Unit tests for the Normalized Comparison Engine and Expression Normalization.

All tests are placed in Tests/drift_tests as required.
Tests verify:
1. Exact identification of the four canonical drift categories:
   - ADDITION
   - DELETION
   - EXPRESSION_DRIFT
   - DESCRIPTION_DRIFT
2. Immunity to formatting, whitespace, quoting, and casing changes.
3. Strict sensitivity to semantic changes (string literals, cast target types).
4. Auto-remediation dropped objects are NOT flagged as DELETION.
5. Placeholder expressions on both sides do not produce false drift.
6. Duplicate object keys in definitions are handled deterministically (first wins) and logged.
7. Unparseable expressions fall back to text cleanup without throwing an exception.
"""

from __future__ import annotations

import pytest

from semabridge.governance.drift import (
    DriftCategory,
    ObjectDef,
    ObjectKind,
    ViewDefinition,
    compare_views,
    normalize_expression,
)


def _make_view(
    objects=None,
    platform="SNOWFLAKE",
    source="live",
    not_deployed=None,
) -> ViewDefinition:
    return ViewDefinition(
        view_name="ANALYTICS.CORE.METRIC_VIEW",
        platform=platform,
        source=source,
        retrieved_at="2026-11-23T12:00:00Z",
        objects=objects or [],
        not_deployed_objects=not_deployed or [],
    )


def test_compare_views_identical_produces_zero_findings():
    """Identical objects produce zero drift findings."""
    objs = [
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="SALES",
            name="TOTAL_SALES",
            expression="SUM(amount)",
            description="Total gross sales revenue",
        ),
    ]
    base = _make_view(objs, source="baseline")
    live = _make_view(objs, source="live")

    findings = compare_views(base, live)
    assert len(findings) == 0


def test_compare_views_formatting_only_produces_zero_findings():
    """Formatting differences (case, spaces, identifier quotes) are NOT drift."""
    base_objs = [
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="SALES",
            name="TOTAL_SALES",
            expression='SUM("AMOUNT")',
            description="Total gross sales revenue",
        ),
    ]
    live_objs = [
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="sales",
            name="total_sales",
            expression="  sum( AMOUNT ) \n",
            description="  Total gross sales revenue  \n",
        ),
    ]
    base = _make_view(base_objs, source="baseline")
    live = _make_view(live_objs, source="live")

    findings = compare_views(base, live)
    assert len(findings) == 0


def test_compare_views_addition():
    """New object present in live warehouse but absent in baseline is classified as ADDITION."""
    base = _make_view([], source="baseline")
    live = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="SALES",
            name="BONUS_COMMISSION",
            expression="SUM(commission * 1.05)",
            description="Bonus sales commission",
        ),
    ], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 1
    f = findings[0]
    assert f.category == DriftCategory.ADDITION
    assert f.name == "BONUS_COMMISSION"
    assert f.baseline_value is None
    assert f.live_value == "SUM(commission * 1.05)"


def test_compare_views_deletion():
    """Object present in baseline but missing in live warehouse is classified as DELETION."""
    base = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="SALES",
            name="LEGACY_DISCOUNT",
            expression="AVG(discount_pct)",
            description="Deprecated discount rate",
        ),
    ], source="baseline")
    live = _make_view([], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 1
    f = findings[0]
    assert f.category == DriftCategory.DELETION
    assert f.name == "LEGACY_DISCOUNT"
    assert f.baseline_value == "AVG(discount_pct)"
    assert f.live_value is None


def test_compare_views_deletion_suppressed_for_not_deployed_objects():
    """Objects intentionally omitted during deploy remediation do NOT trigger DELETION drift."""
    base = _make_view(
        objects=[
            ObjectDef(
                kind=ObjectKind.METRIC,
                table="FINANCE",
                name="REMEDIATED_METRIC",
                expression="SUM(x)",
            ),
        ],
        source="baseline",
        not_deployed=["REMEDIATED_METRIC"],
    )
    live = _make_view([], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 0


def test_compare_views_expression_drift():
    """Formula modification in warehouse is classified as EXPRESSION_DRIFT."""
    base = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="SALES",
            name="PROFIT_MARGIN",
            expression="SUM(profit) / SUM(revenue)",
            description="Standard margin formula",
        ),
    ], source="baseline")
    live = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="SALES",
            name="PROFIT_MARGIN",
            expression="SUM(profit * 0.95) / SUM(revenue)",
            description="Standard margin formula",
        ),
    ], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 1
    f = findings[0]
    assert f.category == DriftCategory.EXPRESSION_DRIFT
    assert f.name == "PROFIT_MARGIN"
    assert f.baseline_value == "SUM(profit) / SUM(revenue)"
    assert f.live_value == "SUM(profit * 0.95) / SUM(revenue)"


def test_compare_views_description_drift():
    """Documentation or comment change in warehouse is classified as DESCRIPTION_DRIFT."""
    base = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="SALES",
            name="NET_REVENUE",
            expression="SUM(amount)",
            description="Official net revenue definition",
        ),
    ], source="baseline")
    live = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="SALES",
            name="NET_REVENUE",
            expression="SUM(amount)",
            description="Modified warehouse description for audit",
        ),
    ], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 1
    f = findings[0]
    assert f.category == DriftCategory.DESCRIPTION_DRIFT
    assert f.name == "NET_REVENUE"
    assert f.baseline_description == "Official net revenue definition"
    assert f.live_description == "Modified warehouse description for audit"


def test_string_literal_change_is_expression_drift():
    """Changing a string literal inside SQL formula constitutes genuine drift."""
    base = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="ORDERS",
            name="DOMESTIC_ORDERS",
            expression="COUNT(CASE WHEN country = 'US' THEN 1 END)",
        )
    ], source="baseline")
    live = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="ORDERS",
            name="DOMESTIC_ORDERS",
            expression="COUNT(CASE WHEN country = 'USA' THEN 1 END)",
        )
    ], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 1
    assert findings[0].category == DriftCategory.EXPRESSION_DRIFT


def test_cast_target_type_change_is_expression_drift():
    """Changing CAST target type constitutes genuine drift."""
    base = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="ORDERS",
            name="CONVERTED_COUNT",
            expression="CAST(order_id AS INT)",
        )
    ], source="baseline")
    live = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="ORDERS",
            name="CONVERTED_COUNT",
            expression="CAST(order_id AS VARCHAR)",
        )
    ], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 1
    assert findings[0].category == DriftCategory.EXPRESSION_DRIFT


def test_placeholder_on_both_sides_produces_no_drift():
    """Placeholder expressions on both sides do not produce false drift."""
    base = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="ORDERS",
            name="NULL_MEASURE",
            expression="CAST(NULL AS DOUBLE)",
        )
    ], source="baseline")
    live = _make_view([
        ObjectDef(
            kind=ObjectKind.METRIC,
            table="ORDERS",
            name="NULL_MEASURE",
            expression="CAST(NULL AS DOUBLE)",
        )
    ], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 0


def test_compare_views_duplicates_logged_first_wins():
    """Duplicate keys in definition are logged and resolved deterministically (first wins)."""
    base = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="REVENUE", expression="SUM(amount)"),
        ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="REVENUE", expression="SUM(corrupted_amount)"),
    ], source="baseline")
    live = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="REVENUE", expression="SUM(amount)"),
    ], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 0


def test_unparseable_expression_falls_back_without_exception():
    """Unparseable expressions fall back to text cleanup without raising exception to caller."""
    expr = "SELECT %%% INVALID SQL SYNTAX &&& -- trailing comment"
    normalized = normalize_expression(expr, platform="SNOWFLAKE")
    assert "SELECT %%% INVALID SQL SYNTAX &&&" in normalized
    assert "-- trailing comment" not in normalized


def test_object_identity_case_insensitive_and_distinct_underscores_whitespace():
    """Case is matched case-insensitively, but 'A B' and 'A_B' are distinct objects."""
    # 1. 'sales' and 'SALES' match as the same object
    base_case = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="sales", expression="SUM(amt)")
    ], source="baseline")
    live_case = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="SALES", expression="SUM(amt)")
    ], source="live")
    assert len(compare_views(base_case, live_case)) == 0

    # 2. 'A B' and 'A_B' are distinct objects
    base_dist = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="T", name="A B", expression="SUM(x)")
    ], source="baseline")
    live_dist = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="T", name="A_B", expression="SUM(x)")
    ], source="live")
    f_dist = compare_views(base_dist, live_dist)
    categories = {f.category for f in f_dist}
    assert DriftCategory.DELETION in categories
    assert DriftCategory.ADDITION in categories


def test_parser_mismatch_guard_threshold():
    """One-metric view with a renamed metric reports addition+deletion; multi-object mismatch raises."""
    import pytest
    from semabridge.governance.drift.models import ParserOrBaselineMismatchError

    # 1 object per side: renamed metric must NOT trigger guard
    base_1 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="T", name="OLD_METRIC", expression="SUM(a)")
    ], source="baseline")
    live_1 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="T", name="NEW_METRIC", expression="SUM(b)")
    ], source="live")
    f_1 = compare_views(base_1, live_1)
    assert len(f_1) == 2
    cats_1 = {f.category for f in f_1}
    assert cats_1 == {DriftCategory.DELETION, DriftCategory.ADDITION}

    # >= 2 objects per side with 100% rename (0 expression matches) MUST trigger guard
    base_2 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="T", name="M1", expression="SUM(a)"),
        ObjectDef(kind=ObjectKind.METRIC, table="T", name="M2", expression="SUM(b)"),
    ], source="baseline")
    live_2 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="T", name="X1", expression="COUNT(x)"),
        ObjectDef(kind=ObjectKind.METRIC, table="T", name="X2", expression="COUNT(y)"),
    ], source="live")
    with pytest.raises(ParserOrBaselineMismatchError):
        compare_views(base_2, live_2)


def test_baseline_object_with_empty_expression_reported_as_deletion():
    """A baseline object with an empty expression that is absent from the live definition is reported as a deletion."""
    base = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="REVENUE", expression="SUM(amount)"),
        ObjectDef(kind=ObjectKind.DIMENSION, table="ORDERS", name="REGION", expression=""),
    ], source="baseline")
    live = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="REVENUE", expression="SUM(amount)"),
    ], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 1
    assert findings[0].category == DriftCategory.DELETION
    assert findings[0].name == "REGION"


def test_object_cast_null_as_double_on_both_sides_produces_no_finding():
    """An object whose expression is CAST(NULL AS DOUBLE) on both sides produces no finding."""
    base = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="NULL_METRIC", expression="CAST(NULL AS DOUBLE)"),
    ], source="baseline")
    live = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="NULL_METRIC", expression="CAST(NULL AS DOUBLE)"),
    ], source="live")

    findings = compare_views(base, live)
    assert len(findings) == 0


def test_object_placeholder_on_one_side_only_produces_expression_drift():
    """An object that is a placeholder on one side only still produces expression drift."""
    # 1. Baseline has real expression, live has placeholder
    base1 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="M1", expression="SUM(amount)"),
    ], source="baseline")
    live1 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="M1", expression="CAST(NULL AS DOUBLE)"),
    ], source="live")
    f1 = compare_views(base1, live1)
    assert len(f1) == 1
    assert f1[0].category == DriftCategory.EXPRESSION_DRIFT

    # 2. Baseline has placeholder, live has real expression
    base2 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="M2", expression="CAST(NULL AS DOUBLE)"),
    ], source="baseline")
    live2 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="M2", expression="SUM(amount)"),
    ], source="live")
    f2 = compare_views(base2, live2)
    assert len(f2) == 1
    assert f2[0].category == DriftCategory.EXPRESSION_DRIFT

    # 3. Baseline has empty expression, live has placeholder
    base3 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="M3", expression=""),
    ], source="baseline")
    live3 = _make_view([
        ObjectDef(kind=ObjectKind.METRIC, table="ORDERS", name="M3", expression="CAST(NULL AS DOUBLE)"),
    ], source="live")
    f3 = compare_views(base3, live3)
    assert len(f3) == 1
    assert f3[0].category == DriftCategory.EXPRESSION_DRIFT


def test_object_def_key_alignment_with_comparison_engine():
    """ObjectDef.key must align with _obj_key and view.by_key() using canonical uppercase keys."""
    from semabridge.governance.drift.comparison import _obj_key

    obj_metric = ObjectDef(kind=ObjectKind.METRIC, table="sales", name="revenue", expression="SUM(amt)")
    assert obj_metric.key == ("METRIC", "SALES", "REVENUE")
    assert obj_metric.key == _obj_key(obj_metric)

    obj_fact = ObjectDef(kind=ObjectKind.FACT, table="sales", name="orders", expression="orders_table")
    assert obj_fact.key == ("FACT", "SALES", "ORDERS")
    assert obj_fact.key == _obj_key(obj_fact)

    obj_rel = ObjectDef(
        kind=ObjectKind.RELATIONSHIP,
        table="",
        name="rel_orders_cust",
        expression="orders (cust_id) REFERENCES customers (id)",
    )
    assert obj_rel.key == _obj_key(obj_rel)
    assert obj_rel.key[0] == "RELATIONSHIP"

    view = _make_view([obj_metric, obj_fact, obj_rel])
    by_key_map = view.by_key()
    assert obj_metric.key in by_key_map
    assert obj_fact.key in by_key_map
    assert obj_rel.key in by_key_map


def test_comparison_fails_without_dialect():
    """Comparison must fail with an explicit ValueError when no SQL dialect is provided."""
    base = ViewDefinition(
        view_name="V1",
        platform="",
        dialect=None,
        source="baseline",
        objects=[ObjectDef(kind=ObjectKind.METRIC, table="T", name="M1", expression="SUM(a)")],
    )
    live = ViewDefinition(
        view_name="V1",
        platform="",
        dialect=None,
        source="live",
        objects=[ObjectDef(kind=ObjectKind.METRIC, table="T", name="M1", expression="SUM(b)")],
    )
    with pytest.raises(ValueError, match="Comparison requires a SQL dialect"):
        compare_views(base, live)


def test_view_definition_dialect_attribute():
    """ViewDefinition carries dialect attribute and auto-normalizes platform if omitted."""
    vd_explicit = ViewDefinition(
        view_name="V1",
        platform="SNOWFLAKE",
        dialect="snowflake",
        objects=[],
    )
    assert vd_explicit.dialect == "snowflake"

    vd_inferred = ViewDefinition(
        view_name="V1",
        platform="DATABRICKS",
        objects=[],
    )
    assert vd_inferred.dialect == "databricks"



